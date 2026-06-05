# 회의 모드 원격 마이크 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/meet phone` 로 회의 모드에서 폰(원격) 마이크를 쓸 수 있게 한다 — RealtimeSTT 를 `use_microphone=False` 로 만들고 MicRouter tap 으로 폰 오디오를 `feed_audio` 한다.

**Architecture:** `MicRouter` 에 raw-프레임 tap 을 추가해, tap 이 설정되면 원격 프레임이 메인 VAD 큐 대신 회의 세션으로 우회한다. `MeetingSession` 은 `use_remote` 면 마이크를 안 잡고 `feed_remote()` 로 주입받는다. `/meet phone|system` 으로 진입 시 소스를 고정 선택한다.

**Tech Stack:** Python 3.11 (asyncio, RealtimeSTT 1.0.2 `feed_audio`/`use_microphone`, pytest).

---

## 파일 구조

| 파일 | 변경 |
|------|------|
| `mic_source.py` (`MicRouter`) | `set_tap(fn)`/`_tap`, `on_remote_frame` 우회, `active` 프로퍼티 |
| `live_translate.py` (`MeetingSession`) | `use_remote` 파라미터, recorder 분기, `feed_remote()` |
| `commands.py` (`/meet`) | `phone`/`system` 인자 → `start_meeting(use_remote)` |
| `main.py` | `start_meeting_setup(use_remote)` → `_begin_meeting(meta, use_remote)`, tap 설정/해제, 경고 |
| `tests/test_mic_router.py` | tap 우회 테스트 추가 |
| `tests/test_meeting_session.py` | 신규 — use_remote/feed_remote |
| `tests/test_meet_command.py` | 신규 — /meet 인자 |

전제: `cd /Users/oracle/Documents/concode/jarvis`, pytest 는 `.venv/bin/python -m pytest`.

---

## Task 1: MicRouter — set_tap + on_remote_frame 우회 + active 프로퍼티

tap 이 설정되면 원격 raw 프레임을 tap 으로 보내고 메인 큐를 우회한다. 회의 모드에서 폰 오디오를 RealtimeSTT 로 보낼 때 사용.

**Files:**
- Modify: `mic_source.py` (`MicRouter`)
- Test: `tests/test_mic_router.py` (추가)

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_mic_router.py` 끝에 추가:
```python
def test_tap_diverts_remote_frames_and_bypasses_queue():
    q = queue.Queue()
    tapped = []
    fed = []

    class Rem:
        def feed(self, b): fed.append(b)
        def reset(self): pass

    r = MicRouter(q, local=_FakeLocal(), remote=Rem())
    r.set_override("remote")          # tap 없으면 큐로 갈 상황
    r.set_tap(tapped.append)
    r.on_remote_frame(b"\x01\x02")
    assert tapped == [b"\x01\x02"]    # tap 으로 우회
    assert fed == []                  # remote.feed 안 탐
    assert q.empty()                  # 메인 큐 미적재

    r.set_tap(None)                   # 해제 → 기존 경로 복귀
    r.on_remote_frame(b"\x03\x04")
    assert tapped == [b"\x01\x02"]    # tap 은 더 안 늘어남
    assert fed == [b"\x03\x04"]       # remote.feed 로 감


def test_active_property():
    q = queue.Queue()
    r = MicRouter(q, local=_FakeLocal(), remote=_FakeRemote())
    assert r.active == "local"
    r.set_override("remote")
    assert r.active == "remote"
```
(`_FakeLocal`/`_FakeRemote` 는 이 파일에 이미 있음.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_mic_router.py::test_tap_diverts_remote_frames_and_bypasses_queue -v`
Expected: FAIL — `AttributeError: 'MicRouter' object has no attribute 'set_tap'`

- [ ] **Step 3: 구현 (mic_source.py)**

(a) `MicRouter.__init__` 에서 `self._suppressed = False` 다음 줄에 추가:
```python
        self._tap = None   # 설정되면 원격 raw 프레임을 여기로 우회(회의 모드 등)
```

(b) `on_remote_frame` 을 교체. 현재:
```python
    def on_remote_frame(self, pcm_bytes):
        if self._suppressed:
            return
        self.note_remote_activity(self._clock())
        self.remote.feed(pcm_bytes)
```
교체 후:
```python
    def on_remote_frame(self, pcm_bytes):
        if self._tap is not None:
            # 회의 모드 등 외부 소비자로 raw 프레임 우회 (메인 VAD 큐로 안 감)
            self._tap(pcm_bytes)
            return
        if self._suppressed:
            return
        self.note_remote_activity(self._clock())
        self.remote.feed(pcm_bytes)
```

(c) `set_tap` 메서드 + `active` 프로퍼티 추가 (예: `on_remote_frame` 아래):
```python
    def set_tap(self, fn):
        """원격 raw 프레임을 외부 소비자로 우회. None 으로 해제(기존 경로 복귀)."""
        self._tap = fn

    @property
    def active(self):
        return self._active
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_mic_router.py -v`
Expected: PASS (전부). 그리고 `.venv/bin/python -m pytest tests/ -q` → 0 failed.

- [ ] **Step 5: 커밋**

```bash
git add mic_source.py tests/test_mic_router.py
git commit -m "feat: MicRouter.set_tap — 원격 프레임 우회(회의 모드용) + active 프로퍼티"
```

---

## Task 2: MeetingSession — use_remote + feed_remote

`use_remote` 면 RealtimeSTT 가 마이크를 안 잡고, `feed_remote(pcm_bytes)` 로 폰 오디오를 주입받는다.

**Files:**
- Modify: `live_translate.py` (`MeetingSession`)
- Test: `tests/test_meeting_session.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_meeting_session.py`:
```python
# tests/test_meeting_session.py
from live_translate import MeetingSession


def _sess(**kw):
    return MeetingSession(log=lambda *_: None, set_status=lambda *_: None, llm=None, **kw)


def test_use_remote_flag_stored():
    assert _sess(use_remote=True).use_remote is True
    assert _sess().use_remote is False


def test_feed_remote_calls_recorder_feed_audio():
    sess = _sess(use_remote=True)
    calls = []

    class FakeRec:
        def feed_audio(self, chunk, sr): calls.append((chunk, sr))

    sess.recorder = FakeRec()
    sess.feed_remote(b"\x01\x02\x03\x04")
    assert calls == [(b"\x01\x02\x03\x04", 16000)]


def test_feed_remote_noop_without_recorder():
    sess = _sess(use_remote=True)
    sess.recorder = None
    sess.feed_remote(b"\x01\x02")   # 예외 없이 무시
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'use_remote'`

- [ ] **Step 3: 구현 (live_translate.py)**

(a) `MeetingSession.__init__` 시그니처에 `use_remote` 추가. 현재:
```python
    def __init__(self, *, log, set_status, llm,
                 meta: MeetingMeta | None = None,
                 model: str = "small", realtime_model: str = "tiny",
                 language: str = ""):
```
교체:
```python
    def __init__(self, *, log, set_status, llm,
                 meta: MeetingMeta | None = None,
                 model: str = "small", realtime_model: str = "tiny",
                 language: str = "", use_remote: bool = False):
```
그리고 `self.language = language` 다음 줄에 추가:
```python
        self.use_remote = use_remote
```

(b) `start()` 의 recorder 생성부를 분기. 현재:
```python
        mic_idx = _pick_physical_mic()

        # 회의 전용 워드북(wordbook_meet.txt)을 양쪽 모델에 컨디셔닝으로 주입.
        # 평상시 자비스 워드북과 분리해 회의에서만 쓰는 고유명사 모음.
        wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH)

        self.recorder = AudioToTextRecorder(
            model=self.model,
            realtime_model_type=self.realtime_model,
            enable_realtime_transcription=True,
            on_realtime_transcription_update=self._on_partial,
            language=self.language,
            initial_prompt=wb_prompt,
            initial_prompt_realtime=wb_prompt,
            spinner=False,
            post_speech_silence_duration=0.7,
            silero_sensitivity=0.4,
            webrtc_sensitivity=3,
            device="cpu",
            compute_type="int8",
            input_device_index=mic_idx,
            level=30,   # WARNING 만
        )
```
교체:
```python
        # 회의 전용 워드북(wordbook_meet.txt)을 양쪽 모델에 컨디셔닝으로 주입.
        # 평상시 자비스 워드북과 분리해 회의에서만 쓰는 고유명사 모음.
        wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH)

        rec_kwargs = dict(
            model=self.model,
            realtime_model_type=self.realtime_model,
            enable_realtime_transcription=True,
            on_realtime_transcription_update=self._on_partial,
            language=self.language,
            initial_prompt=wb_prompt,
            initial_prompt_realtime=wb_prompt,
            spinner=False,
            post_speech_silence_duration=0.7,
            silero_sensitivity=0.4,
            webrtc_sensitivity=3,
            device="cpu",
            compute_type="int8",
            level=30,   # WARNING 만
        )
        if self.use_remote:
            # 폰(원격) 오디오를 feed_remote()→feed_audio 로 주입 — 마이크 미점유
            rec_kwargs["use_microphone"] = False
        else:
            rec_kwargs["input_device_index"] = _pick_physical_mic()

        self.recorder = AudioToTextRecorder(**rec_kwargs)
```

(c) `feed_remote` 메서드 추가 (예: `start` 위/아래, 클래스 메서드로):
```python
    def feed_remote(self, pcm_bytes) -> None:
        """원격(폰) raw 16kHz Int16 PCM 을 RealtimeSTT 로 주입.
        use_remote 회의에서 MicRouter tap 이 매 프레임 호출한다."""
        if self.recorder is not None:
            self.recorder.feed_audio(pcm_bytes, 16000)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -v`
Expected: PASS (3). 그리고 `.venv/bin/python -c "import live_translate; print('ok')"` → ok.

- [ ] **Step 5: 커밋**

```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "feat: MeetingSession.use_remote — 폰 오디오를 feed_audio 로 주입"
```

---

## Task 3: /meet phone|system 인자

**Files:**
- Modify: `commands.py` (`_meet`)
- Test: `tests/test_meet_command.py` (신규)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_meet_command.py`:
```python
# tests/test_meet_command.py
import asyncio

import commands


def _run(text, ctx):
    asyncio.run(commands.dispatch(text, ctx))


def test_meet_phone_passes_use_remote_true():
    got = {}
    async def starter(use_remote): got["v"] = use_remote
    _run("/meet phone", {"log": lambda *_: None, "start_meeting": starter})
    assert got["v"] is True


def test_meet_system_and_noarg_false():
    got = []
    async def starter(use_remote): got.append(use_remote)
    _run("/meet system", {"log": lambda *_: None, "start_meeting": starter})
    _run("/meet", {"log": lambda *_: None, "start_meeting": starter})
    assert got == [False, False]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meet_command.py -v`
Expected: FAIL — 현재 `_meet` 가 `starter()` 를 인자 없이 호출(`TypeError`) 또는 use_remote 미전달.

- [ ] **Step 3: 구현 — `commands.py` 의 `_meet` 교체**

현재:
```python
@command("meet", help="회의 모드 — 메타 입력 후 실시간 자막 + 양방향 번역")
async def _meet(args: str, ctx: dict):
    starter = ctx.get("start_meeting")
    if starter is None:
        ctx["log"]("이 환경에서는 회의 모드를 사용할 수 없습니다.")
        return
    await starter()
    ctx["handled_state"] = True   # 메타 입력 대기 상태로, idle 막기
```
교체:
```python
@command("meet", help="회의 모드 — 실시간 자막 + 양방향 번역", usage="[phone|system]")
async def _meet(args: str, ctx: dict):
    starter = ctx.get("start_meeting")
    if starter is None:
        ctx["log"]("이 환경에서는 회의 모드를 사용할 수 없습니다.")
        return
    use_remote = args.strip().lower() in ("phone", "remote")
    await starter(use_remote)
    ctx["handled_state"] = True   # 회의 진입 상태로, idle 막기
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meet_command.py -v`
Expected: PASS (2). 그리고 `.venv/bin/python -m pytest tests/ -q` → 0 failed.

- [ ] **Step 5: 커밋**

```bash
git add commands.py tests/test_meet_command.py
git commit -m "feat: /meet phone|system — 회의 소스 선택"
```

---

## Task 4: main.py 배선 — use_remote 전달 + tap 설정/해제 + 경고

**Files:**
- Modify: `main.py`

통합 배선 — import/parse 스모크 + 전체 suite 로 검증.

- [ ] **Step 1: start_meeting_setup 가 use_remote 를 받아 전달**

`main.py` `start_meeting_setup` 의 시그니처와 `_begin_meeting` 호출을 수정. 현재:
```python
    async def start_meeting_setup():
        """/meet 진입 → 메타 입력 단계 시작. 첫 질문 출력."""
        nonlocal response
        if meeting_session["obj"] is not None:
            console.log("회의 모드가 이미 진행 중입니다.")
            return
        if meeting_setup["obj"] is not None:
            return
        await cancel(response); response = None
        player.flush()
        _drain_text_queue()
        from live_translate import MeetingSetup
        setup = MeetingSetup(default_my_name=config.USER_NAME)
        if setup.done:
            # 입력 단계 없음(상대방 이름 안 받음) → 곧장 회의 시작
            await _begin_meeting(setup.meta)
            return
        meeting_setup["obj"] = setup
        console.log(f"🎤 회의 시작 전 정보를 입력해주세요. (내 이름: {config.USER_NAME}, Esc 로 취소)")
        console.log(f"   {setup.prompt}")
```
교체 (시그니처에 `use_remote=False`, `_begin_meeting` 에 전달):
```python
    async def start_meeting_setup(use_remote=False):
        """/meet 진입 → (입력 단계 없으면) 곧장 회의 시작."""
        nonlocal response
        if meeting_session["obj"] is not None:
            console.log("회의 모드가 이미 진행 중입니다.")
            return
        if meeting_setup["obj"] is not None:
            return
        await cancel(response); response = None
        player.flush()
        _drain_text_queue()
        from live_translate import MeetingSetup
        setup = MeetingSetup(default_my_name=config.USER_NAME)
        if setup.done:
            await _begin_meeting(setup.meta, use_remote)
            return
        meeting_setup["obj"] = setup
        console.log(f"🎤 회의 시작 전 정보를 입력해주세요. (내 이름: {config.USER_NAME}, Esc 로 취소)")
        console.log(f"   {setup.prompt}")
```

- [ ] **Step 2: _begin_meeting 이 use_remote 처리 + tap 설정 + 경고**

`_begin_meeting` 의 시그니처와 본문 앞부분을 수정. 현재 시작:
```python
    async def _begin_meeting(meta) -> None:
        """메타가 모인 다음 호출. 본체 마이크 양보 + RealtimeSTT 시작.
        RELAY_URL/RELAY_TOKEN 이 설정돼 있으면 outbound ws 로 자막 중계도 활성."""
        from live_translate import MeetingSession
        mic.pause()
        try:
            sess = MeetingSession(
                log=console.log,
                set_status=console.set_status,
                llm=llm,
                meta=meta,
                model=config.MEET_STT_MODEL,
                realtime_model=config.MEET_STT_REALTIME_MODEL,
            )
            await sess.start()
            meeting_session["obj"] = sess
            console.log(f"🎤 회의를 시작합니다. 회의 번호: {meta.key}")
```
교체:
```python
    async def _begin_meeting(meta, use_remote=False) -> None:
        """메타가 모인 다음 호출. 본체 마이크 양보 + RealtimeSTT 시작.
        use_remote 면 폰(원격) 마이크를 RealtimeSTT 로 먹인다.
        RELAY_URL/RELAY_TOKEN 이 설정돼 있으면 outbound ws 로 자막 중계도 활성."""
        from live_translate import MeetingSession
        if use_remote and not config.REMOTE_MIC_ENABLED:
            console.log("⚠ 원격 마이크가 비활성(REMOTE_MIC_ENABLED) — 시스템 마이크로 진행합니다.")
            use_remote = False
        mic.pause()
        try:
            sess = MeetingSession(
                log=console.log,
                set_status=console.set_status,
                llm=llm,
                meta=meta,
                model=config.MEET_STT_MODEL,
                realtime_model=config.MEET_STT_REALTIME_MODEL,
                use_remote=use_remote,
            )
            await sess.start()
            meeting_session["obj"] = sess
            if use_remote:
                # 폰 raw 프레임을 메인 VAD 대신 RealtimeSTT 로 우회
                mic.router.set_tap(sess.feed_remote)
                if mic.router.active != "remote":
                    console.log("⚠ 폰이 연결돼 있지 않습니다 — 폰에서 마이크를 켜세요.")
            console.log(f"🎤 회의를 시작합니다 (소스: {'폰' if use_remote else '시스템'}). 회의 번호: {meta.key}")
```
(아래 relay 중계 블록은 그대로 둔다.)

- [ ] **Step 3: stop_meeting 에서 tap 해제**

`stop_meeting` 의 정리부를 수정. 현재:
```python
        try:
            await sess.stop()
        finally:
            meeting_session["obj"] = None
            mic.resume()
            console.set_status(None)
            idle()
```
교체:
```python
        try:
            await sess.stop()
        finally:
            mic.router.set_tap(None)   # 원격 프레임을 메인 경로로 복귀
            meeting_session["obj"] = None
            mic.resume()
            console.set_status(None)
            idle()
```

- [ ] **Step 4: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"`
Expected: `ok`
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 0 failed

- [ ] **Step 5: 커밋**

```bash
git add main.py
git commit -m "feat: main — /meet 소스 전달, 폰 모드 tap 설정/해제 + 경고"
```

---

## Task 5: 수동 E2E 확인 (비코드)

- [ ] **Step 1: 폰 모드 회의**

1. jarvis 재시작 (`.env`: REMOTE_MIC_ENABLED=true, RELAY_URL/TOKEN, USER_NAME=Concode).
2. 폰 `…/m/Concode` → admin → 마이크 켜기 (게이지 확인).
3. jarvis 콘솔에서 `/meet phone` → "소스: 폰" 표시.
4. 폰에 대고 말 → 자막/번역 생성 확인.
5. `/stop` → 회의 종료. 이후 평상시 폰 마이크(원격) 정상 동작 확인(`/meet` 안 한 상태에서 "Hey Jarvis").

- [ ] **Step 2: 시스템 모드 회의**

1. `/meet system`(또는 `/meet`) → "소스: 시스템".
2. PC 마이크로 자막 생성 확인.

- [ ] **Step 3: 폰 미연결 경고**

1. 폰 마이크 끈 상태에서 `/meet phone` → "⚠ 폰이 연결돼 있지 않습니다" 경고 확인. 이후 폰을 켜면 자막이 흐르기 시작하는지 확인.

---

## Self-Review 결과

**Spec coverage:**
- `/meet phone|system`/무인자=system → Task 3 ✓
- MicRouter tap 우회 → Task 1 ✓
- MeetingSession use_remote + feed_remote + recorder 분기 → Task 2 ✓
- main 배선(tap 설정/해제, REMOTE_MIC_ENABLED 폴백, 미연결 경고) → Task 4 ✓
- 회의 종료 시 set_tap(None) → Task 4 Step 3 ✓
- 테스트(단위 MicRouter/MeetingSession/명령 + 수동 E2E) → Task 1·2·3 단위, Task 5 수동 ✓
- 연기(동적 전환, 폰 자막/TTS 송출) → 미구현(스펙대로) ✓

**Type consistency:** `MicRouter.set_tap(fn)`/`active`, `on_remote_frame` 우회, `MeetingSession(use_remote=...)`/`feed_remote(pcm_bytes)`(→`feed_audio(bytes,16000)`), `start_meeting(use_remote)`/`_begin_meeting(meta, use_remote)` — Task 간 일치.

**엣지/한계:** tap 은 suppress 보다 우선(회의 중 pause 상태여도 폰 프레임이 feed_remote 로 감). 회의 중 폰 끊김은 recorder 무음 대기(동적 전환 없음). `feed_audio` 는 raw int16 16kHz 그대로 — RealtimeSTT 1.0.2 가 내부 리샘플(original_sample_rate=16000).
