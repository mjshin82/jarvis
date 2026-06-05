# 회의 모드 원격 마이크 (동적) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 모드가 MicRouter 의 현재 활성 소스(시스템/폰)를 동적으로 따르게 한다 — RealtimeSTT 는 항상 `use_microphone=False`, jarvis 가 활성 소스 블록을 `feed_audio` 로 계속 먹임. 회의 중 소스 전환 자유, `/meet` 인자 없음.

**Architecture:** 정적 설계(이 미머지 브랜치에 구현됨)를 개정한다. MicRouter tap 을 원격 raw 대신 **블록 레벨(`_sink_local`/`_sink_remote`)** 로 옮겨 활성 소스 블록을 우회시킨다. MeetingSession 은 `use_microphone=False` 로만 만들고 `feed_block(float32)→int16 bytes→feed_audio` 로 받는다. 회의 진입 시 `mic.pause()` 를 호출하지 않아(시스템 마이크 캡처 유지) tap 만으로 라우팅한다.

**Tech Stack:** Python 3.11 (asyncio, numpy, RealtimeSTT 1.0.2 feed_audio, pytest).

---

## 파일 구조 (개정 대상 — 모두 이미 존재)

| 파일 | 개정 |
|------|------|
| `mic_source.py` (`MicRouter`) | tap 을 `_sink_local`/`_sink_remote` 블록 레벨로 이동, `on_remote_frame` 의 tap 분기 제거 |
| `live_translate.py` (`MeetingSession`) | `use_remote` 제거, 항상 `use_microphone=False`, `feed_remote`→`feed_block`(float32→int16), `_pick_physical_mic`/`import pyaudio` 제거, `import numpy as np` 추가 |
| `commands.py` (`/meet`) | `phone`/`system` 인자 제거 |
| `main.py` | `_begin_meeting(meta)` — pause/gating/경고 제거, `set_tap(sess.feed_block)`, stop 시 `set_tap(None)`; `start_meeting_setup()` 무인자 |
| `tests/test_mic_router.py` | 블록 레벨 tap 테스트로 교체 |
| `tests/test_meeting_session.py` | feed_block 변환 테스트로 교체 |
| `tests/test_meet_command.py` | 무인자 테스트로 교체 |

전제: `cd /Users/oracle/Documents/concode/jarvis`, pytest 는 `.venv/bin/python -m pytest`.

---

## Task 1: MicRouter — tap 을 블록 레벨로 이동

활성 소스의 블록(float32 512)을 tap 으로 우회. `on_remote_frame` 은 tap 분기 제거(원복).

**Files:**
- Modify: `mic_source.py` (`MicRouter`)
- Test: `tests/test_mic_router.py` (기존 tap 테스트 교체)

- [ ] **Step 1: 기존 tap 테스트를 블록 레벨로 교체**

`tests/test_mic_router.py` 의 `test_tap_diverts_remote_frames_and_bypasses_queue` 함수를 통째로 다음으로 **교체**(이름도 변경):
```python
def test_tap_diverts_active_source_blocks_and_bypasses_queue():
    q = queue.Queue()
    tapped = []
    r = MicRouter(q, local=_FakeLocal(), remote=_FakeRemote())
    r.set_tap(tapped.append)

    # local active → _sink_local 블록이 tap 으로 (큐 미적재)
    b1 = _block(0.1)
    r._sink_local(b1)
    assert tapped == [b1]
    assert q.empty()

    # remote active → _sink_remote 블록이 tap 으로
    r.set_override("remote")
    b2 = _block(0.2)
    r._sink_remote(b2)
    assert tapped == [b1, b2]
    assert q.empty()

    # 비활성 소스 블록은 무시 (active=remote 인데 local sink 호출)
    r._sink_local(_block(0.9))
    assert tapped == [b1, b2]

    # tap 해제 → 큐로 복귀
    r.set_tap(None)
    r._sink_remote(_block(0.3))
    assert q.qsize() == 1
```
(`_block`, `_FakeLocal`, `_FakeRemote` 는 이 파일에 이미 있음. `test_active_property` 는 그대로 둔다.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_mic_router.py::test_tap_diverts_active_source_blocks_and_bypasses_queue -v`
Expected: FAIL — 현재 `_sink_local`/`_sink_remote` 는 tap 을 모름(블록이 tap 으로 안 감).

- [ ] **Step 3: 구현 (mic_source.py)**

(a) `_sink_local`/`_sink_remote` 를 교체. 현재:
```python
    def _sink_local(self, block):
        if self._active == "local":
            self._q.put(block)

    def _sink_remote(self, block):
        if self._active == "remote":
            self._q.put(block)
```
교체:
```python
    def _sink_local(self, block):
        if self._active != "local":
            return
        if self._tap is not None:          # 회의 모드: 활성 소스 블록을 우회
            self._tap(block)
            return
        self._q.put(block)

    def _sink_remote(self, block):
        if self._active != "remote":
            return
        if self._tap is not None:
            self._tap(block)
            return
        self._q.put(block)
```

(b) `on_remote_frame` 에서 tap 분기 제거(원복). 현재:
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
교체:
```python
    def on_remote_frame(self, pcm_bytes):
        if self._suppressed:
            return
        self.note_remote_activity(self._clock())
        self.remote.feed(pcm_bytes)
```
(`set_tap`/`active`/`_tap` 필드는 그대로 둔다.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_mic_router.py -v`
Expected: PASS (전부). 그리고 `.venv/bin/python -m pytest tests/ -q` → 0 failed.

- [ ] **Step 5: 커밋**

```bash
git add mic_source.py tests/test_mic_router.py
git commit -m "refactor: MicRouter tap 을 블록 레벨로 — 회의가 활성 소스를 동적 추종"
```

---

## Task 2: MeetingSession — 항상 use_microphone=False + feed_block

`use_remote` 제거, recorder 는 항상 마이크 미점유, float32 블록을 int16 으로 변환해 주입.

**Files:**
- Modify: `live_translate.py` (`MeetingSession`)
- Test: `tests/test_meeting_session.py` (교체)

- [ ] **Step 1: 테스트 교체**

`tests/test_meeting_session.py` 전체를 다음으로 교체:
```python
# tests/test_meeting_session.py
import numpy as np

from live_translate import MeetingSession


def _sess(**kw):
    return MeetingSession(log=lambda *_: None, set_status=lambda *_: None, llm=None, **kw)


def test_feed_block_converts_float32_to_int16_bytes():
    sess = _sess()
    calls = []

    class FakeRec:
        def feed_audio(self, chunk, sr): calls.append((chunk, sr))

    sess.recorder = FakeRec()
    sess.feed_block(np.array([0.5, -0.5, 0.0, 1.0], dtype=np.float32))
    assert len(calls) == 1
    chunk, sr = calls[0]
    assert sr == 16000
    arr = np.frombuffer(chunk, dtype="<i2")
    assert arr[0] == 16383      # 0.5 * 32767 → 16383 (절삭)
    assert arr[1] == -16383
    assert arr[2] == 0
    assert arr[3] == 32767      # 1.0 클립


def test_feed_block_noop_without_recorder():
    sess = _sess()
    sess.recorder = None
    sess.feed_block(np.zeros(4, dtype=np.float32))   # 예외 없이 무시


def test_no_use_remote_param():
    # use_remote 파라미터는 제거됨 — 주면 TypeError
    import pytest
    with pytest.raises(TypeError):
        _sess(use_remote=True)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -v`
Expected: FAIL — `feed_block` 없음 / 아직 `use_remote` 받음.

- [ ] **Step 3: 구현 (live_translate.py)**

(a) 상단 import 에 numpy 추가(없으면). 현재 import 부에 `import numpy as np` 추가. `import pyaudio` 는 제거.

(b) `__init__` 에서 `use_remote` 제거. 현재:
```python
    def __init__(self, *, log, set_status, llm,
                 meta: MeetingMeta | None = None,
                 model: str = "small", realtime_model: str = "tiny",
                 language: str = "",
                 use_remote: bool = False):
```
교체:
```python
    def __init__(self, *, log, set_status, llm,
                 meta: MeetingMeta | None = None,
                 model: str = "small", realtime_model: str = "tiny",
                 language: str = ""):
```
그리고 본문의 `self.use_remote = use_remote` 줄을 **삭제**.

(c) `feed_remote` 메서드를 `feed_block` 으로 교체. 현재:
```python
    def feed_remote(self, pcm_bytes) -> None:
        """원격(폰) raw 16kHz Int16 PCM 을 RealtimeSTT 로 주입.
        use_remote 회의에서 MicRouter tap 이 매 프레임 호출한다."""
        if self.recorder is not None:
            self.recorder.feed_audio(pcm_bytes, 16000)
```
교체:
```python
    def feed_block(self, block) -> None:
        """MicRouter tap 이 매 블록 호출 — float32 [-1,1] 16kHz 블록을
        int16 PCM bytes 로 변환해 RealtimeSTT 에 주입.
        (numpy float32 를 그대로 feed_audio 에 주면 astype(int16) 로 0 이 됨)"""
        if self.recorder is None:
            return
        pcm16 = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        self.recorder.feed_audio(pcm16, 16000)
```

(d) `start()` 의 recorder 분기를 제거하고 항상 `use_microphone=False`. 현재:
```python
        if self.use_remote:
            # 폰(원격) 오디오를 feed_remote()→feed_audio 로 주입 — 마이크 미점유
            rec_kwargs["use_microphone"] = False
        else:
            rec_kwargs["input_device_index"] = _pick_physical_mic()

        self.recorder = AudioToTextRecorder(**rec_kwargs)
```
교체:
```python
        # RealtimeSTT 는 장치를 직접 잡지 않는다 — jarvis 가 feed_block 으로 먹인다.
        rec_kwargs["use_microphone"] = False

        self.recorder = AudioToTextRecorder(**rec_kwargs)
```

(e) `_pick_physical_mic` 함수 정의를 **삭제**(이제 호출처 없음). 함수 전체:
```python
def _pick_physical_mic() -> int | None:
    ...
```
를 제거한다. (제거 후 `grep -n "_pick_physical_mic\|pyaudio" live_translate.py` 가 빈 결과여야 함.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -v` (3 pass).
Run: `.venv/bin/python -c "import live_translate; print('ok')"` (ok).
Run: `grep -n "_pick_physical_mic\|pyaudio" live_translate.py` → 빈 결과.
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed.

- [ ] **Step 5: 커밋**

```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "refactor: MeetingSession 항상 use_microphone=False + feed_block(float32→int16)"
```

---

## Task 3: /meet — 소스 인자 제거

**Files:**
- Modify: `commands.py` (`_meet`)
- Test: `tests/test_meet_command.py` (교체)

- [ ] **Step 1: 테스트 교체**

`tests/test_meet_command.py` 전체를 다음으로 교체:
```python
# tests/test_meet_command.py
import asyncio

import commands


def _run(text, ctx):
    asyncio.run(commands.dispatch(text, ctx))


def test_meet_calls_starter_no_arg():
    called = []
    async def starter(): called.append(True)
    _run("/meet", {"log": lambda *_: None, "start_meeting": starter})
    assert called == [True]


def test_meet_ignores_extra_args():
    called = []
    async def starter(): called.append(True)
    _run("/meet phone", {"log": lambda *_: None, "start_meeting": starter})
    assert called == [True]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meet_command.py -v`
Expected: FAIL — 현재 `_meet` 가 `starter(use_remote)` 로 호출(인자 1개) → `starter()` 시그니처와 불일치(TypeError).

- [ ] **Step 3: 구현 — `commands.py` 의 `_meet` 교체**

현재:
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
교체:
```python
@command("meet", help="회의 모드 — 실시간 자막 + 양방향 번역")
async def _meet(args: str, ctx: dict):
    starter = ctx.get("start_meeting")
    if starter is None:
        ctx["log"]("이 환경에서는 회의 모드를 사용할 수 없습니다.")
        return
    await starter()
    ctx["handled_state"] = True   # 회의 진입 상태로, idle 막기
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meet_command.py -v` (2 pass). 그리고 `.venv/bin/python -m pytest tests/ -q` → 0 failed.

- [ ] **Step 5: 커밋**

```bash
git add commands.py tests/test_meet_command.py
git commit -m "refactor: /meet 소스 인자 제거 — 회의가 현재 마이크 소스 추종"
```

---

## Task 4: main.py — 동적 배선 (pause 제거 + tap=feed_block)

**Files:**
- Modify: `main.py`

통합 배선 — import/parse 스모크 + 전체 suite.

- [ ] **Step 1: start_meeting_setup 무인자로**

현재:
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
            # 입력 단계 없음(상대방 이름 안 받음) → 곧장 회의 시작
            await _begin_meeting(setup.meta, use_remote)
            return
```
교체(시그니처 무인자, `_begin_meeting(setup.meta)`):
```python
    async def start_meeting_setup():
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
            # 입력 단계 없음(상대방 이름 안 받음) → 곧장 회의 시작
            await _begin_meeting(setup.meta)
            return
```

- [ ] **Step 2: _begin_meeting 동적화 (pause/gating/경고 제거, tap=feed_block)**

현재 시작부(시그니처 ~ `console.log(f"🎤 회의를 시작합니다 ...")` 까지):
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
교체:
```python
    async def _begin_meeting(meta) -> None:
        """메타가 모인 다음 호출. RealtimeSTT 시작 + MicRouter tap 으로 현재 활성
        소스(시스템/폰)를 동적으로 먹인다. mic.pause() 안 함 — 로컬 캡처를 유지해야
        시스템 마이크를 feed 할 수 있고, tap 이 블록을 큐에서 가로채 wake/VAD 는 idle.
        RELAY_URL/RELAY_TOKEN 이 설정돼 있으면 outbound ws 로 자막 중계도 활성."""
        from live_translate import MeetingSession
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
            # 활성 소스 블록을 메인 VAD 대신 RealtimeSTT 로 우회 (소스 전환은 동적)
            mic.router.set_tap(sess.feed_block)
            console.log(f"🎤 회의를 시작합니다. 회의 번호: {meta.key}")
```
(이후 relay 중계 블록은 그대로. 단 아래 except 도 수정 — 다음 스텝.)

- [ ] **Step 3: _begin_meeting 의 except 에서 mic.resume 제거 + tap 정리**

현재 `_begin_meeting` 끝의 except:
```python
        except Exception as ex:
            mic.resume()
            console.log(f"회의 모드 시작 실패: {ex}")
```
교체(시작 실패 시 tap 은 아직 미설정이지만 방어적으로 해제, resume 제거):
```python
        except Exception as ex:
            mic.router.set_tap(None)
            console.log(f"회의 모드 시작 실패: {ex}")
```

- [ ] **Step 4: stop_meeting 에서 resume 제거 (tap 해제는 유지)**

현재:
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
교체(회의 중 pause 안 했으므로 resume 불필요):
```python
        try:
            await sess.stop()
        finally:
            mic.router.set_tap(None)   # 블록을 메인 큐(wake/VAD)로 복귀
            meeting_session["obj"] = None
            console.set_status(None)
            idle()
```

- [ ] **Step 5: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed
Run: `grep -n "use_remote\|feed_remote\|mic.pause\|mic.resume" main.py` → 빈 결과(회의 관련 잔여 없음; 다른 모드의 pause/resume 가 없다면 전부 빔)

- [ ] **Step 6: 커밋**

```bash
git add main.py
git commit -m "refactor: main 회의 동적 배선 — pause 제거, tap=feed_block, 인자 제거"
```

---

## Task 5: 수동 E2E

- [ ] **Step 1: 시스템 소스 회의**

1. jarvis 재시작. 폰 마이크 끈 상태에서 `/meet` → 회의 시작.
2. PC(시스템) 마이크로 말 → 자막 생성 확인. (active=local 블록이 feed)

- [ ] **Step 2: 회의 중 폰으로 동적 전환**

1. 회의 중 폰 `…/m/Concode` 마이크 켜기(또는 콘솔 `/mic phone`).
2. jarvis 콘솔 `🎙️ 입력 소스 → 원격(폰)` 후, 자막 소스가 폰으로 **끊김 없이** 전환되는지 확인.
3. `/mic system`(또는 폰 끄기) → 시스템으로 복귀, 자막 계속.

- [ ] **Step 3: 종료 복귀**

1. `/stop` → 회의 종료. 평상시 "Hey Jarvis"(원격/시스템) 정상 동작 확인.

---

## Self-Review 결과

**Spec coverage:**
- tap 블록 레벨 + on_remote_frame 원복 → Task 1 ✓
- 항상 use_microphone=False + feed_block(float32→int16) + _pick_physical_mic/pyaudio 제거 → Task 2 ✓
- /meet 인자 제거 → Task 3 ✓
- main: pause/gating/경고 제거, set_tap(feed_block), stop 시 set_tap(None) → Task 4 ✓
- 동적 전환(회의 중 /mic·auto), 폰 없으면 시스템 자동 → Task 1+4 (tap 이 활성 소스 따름) ✓
- 테스트(단위 3 + 수동) → Task 1·2·3 단위, Task 5 수동 ✓
- 연기(폰 자막/TTS 송출) → 미구현 ✓

**Type consistency:** `MicRouter._sink_*` tap, `set_tap`/`active` 유지 · `MeetingSession.feed_block(block)`(float32→int16 bytes→`feed_audio(bytes,16000)`) · `/meet`→`start_meeting()` 무인자 · `_begin_meeting(meta)` 무 use_remote · `mic.router.set_tap(sess.feed_block)` 일치.

**핵심 불변식:** 회의 중 `mic.pause()` 안 함 → LocalMicSource 캡처 유지 → active=local 시 시스템 마이크가 feed. tap 이 블록을 큐에서 가로채 wake/VAD idle. 종료 시 set_tap(None) 로 복귀. feed_block 은 float32 를 ×32767 후 int16(직접 astype 금지 — 무음).
