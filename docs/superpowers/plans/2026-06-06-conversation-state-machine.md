# 대화 상태 머신 (ConversationController) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `main()` 에 흩어진 대화 상태와 4겹 모드를 단일 `ConversationController`(conversation.py)로 추출하고, 모든 전환을 `_teardown → _enter` 단일 경로로 모아 누락 전이·불가능 조합을 구조적으로 막는다.

**Architecture:** 최상위 `Mode`(IDLE/CONVERSING/TRANSLATE/MEETING) enum 머신을 가진 컨트롤러가 모든 대화 상태를 소유한다. 오디오/네트워크 협력자는 생성자로 주입(DI)되어 하드웨어 없이 단위 테스트 가능하다. Phase 1 에서 컨트롤러를 fake 로 완성·검증하고(main 무변경), Phase 2 에서 `main()` 을 위임 구조로 교체한다.

**Tech Stack:** Python 3.11, asyncio, pytest (`.venv/bin/python -m pytest`), numpy. 기존 테스트 러너 관례: pytest-asyncio 미사용 — 비동기 테스트는 `asyncio.run(...)` 래퍼로 작성.

**스펙:** `docs/superpowers/specs/2026-06-06-conversation-state-machine-design.md`

---

## 컨트롤러 최종 형태 (참조 — 태스크들이 이걸 점진적으로 구축)

```python
# conversation.py
import asyncio, enum, time

class Mode(enum.Enum):
    IDLE = "idle"; CONVERSING = "conversing"; TRANSLATE = "translate"; MEETING = "meeting"

class Phase(enum.Enum):
    LISTENING = "listening"; RESPONDING = "responding"

class MeetingPhase(enum.Enum):
    SETUP = "setup"; LIVE = "live"
```

생성자 주입 포트(고정 — 모든 태스크가 이 이름을 사용):
`mic, recognizer, player, web_pub=None, log, set_status, speak, transcribe,
translate_audio, mode_intent, translate_mode, make_setup, make_meeting,
after_meeting_start, dispatch_command, fx, follow_up=True, listen_timeout_s=8.0,
clock=time.monotonic`

상태 필드: `mode, phase, meeting_phase, meeting_session, meeting_setup,
saved_mic_mode, hands_free, stop_after_response, response, watchdog, _output_busy_until`.

협력자 계약(인터페이스):
- `mic`: `set_tap(fn|None)`, `set_override(str)`, `snapshot_mode()->str`, `restore_mode(str)`
- `recognizer`: `feed_block(block)` (또는 None)
- `player`: `is_speaking()->bool`, `enqueue_file(path) [async]`, `flush()`
- `web_pub`: `emit(kind, text="")` (또는 None)
- `speak(text)`: async — LLM→TTS→웹; 내부에서 user 로그·emit 까지 함(기존 speak_response)
- `transcribe(audio)`: async → str
- `translate_audio(audio)`: async → None (기존 `_translate_bg` 본문)
- `mode_intent(text)`: → `"meeting"|"stop"|None`
- `translate_mode`: `is_translate()`, `start_translate(lang)`, `end_translate()`
- `make_setup()`: → MeetingSetup(`.done`, `.prompt`, `.submit(str)`, `.meta`)
- `make_meeting(meta)`: → MeetingSession(`await start()`, `await stop()`, `feed_block`)
- `after_meeting_start(sess)`: sync 훅 — web listener 등록 + 자막 URL 로그
- `dispatch_command(line)`: async → bool (명령이 상태를 점유했으면 True)
- `fx`: `{"wake": path, "ok": path}`

---

## Task 1: conversation.py 골격 + Mode/Phase enum + 생성자 + 테스트 fake

**Files:**
- Create: `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_conversation.py`

```python
import asyncio
import numpy as np
from conversation import ConversationController, Mode, Phase, MeetingPhase


# ---- fake 협력자 ----
class FakeMic:
    def __init__(self):
        self.tap = None; self.override = None
        self.snapshots = 0; self.restored = []; self._mode = "auto"
    def set_tap(self, fn): self.tap = fn
    def set_override(self, m): self.override = m; self._mode = m
    def snapshot_mode(self): self.snapshots += 1; return self._mode
    def restore_mode(self, m): self.restored.append(m); self._mode = m

class FakeRecognizer:
    def __init__(self): self.fed = []
    def feed_block(self, b): self.fed.append(b)

class FakePlayer:
    def __init__(self): self.speaking = False; self.files = []; self.flushed = 0
    def is_speaking(self): return self.speaking
    def flush(self): self.flushed += 1
    async def enqueue_file(self, p): self.files.append(p)

class FakeWebPub:
    def __init__(self): self.emits = []
    def emit(self, kind, text=""): self.emits.append((kind, text))

class FakeSession:
    def __init__(self): self.started = False; self.stopped = False; self.fed = []
    async def start(self): self.started = True
    async def stop(self): self.stopped = True
    def feed_block(self, b): self.fed.append(b)

class FakeSetup:
    def __init__(self, done=True, meta="META"):
        self.done = done; self.prompt = "상대 이름?"; self.meta = meta; self.submitted = []
    def submit(self, s): self.submitted.append(s); self.done = True


def make_controller(**over):
    spans = {}
    async def speak(t): spans.setdefault("speak", []).append(t)
    async def transcribe(a): return spans.get("stt", "안녕")
    async def translate_audio(a): spans.setdefault("tx", []).append(a)
    def mode_intent(t): return spans.get("intent")
    async def dispatch_command(line): return spans.get("handled", False)
    class TM:
        def __init__(s): s.on = False; s.lang = None
        def is_translate(s): return s.on
        def start_translate(s, l): s.on = True; s.lang = l
        def end_translate(s): s.on = False; s.lang = None
    deps = dict(
        mic=FakeMic(), recognizer=FakeRecognizer(), player=FakePlayer(),
        web_pub=FakeWebPub(), log=lambda *a: None, set_status=lambda *a: None,
        speak=speak, transcribe=transcribe, translate_audio=translate_audio,
        mode_intent=mode_intent, translate_mode=TM(),
        make_setup=lambda: FakeSetup(), make_meeting=lambda meta: FakeSession(),
        after_meeting_start=lambda sess: spans.setdefault("after", []).append(sess),
        dispatch_command=dispatch_command, fx={"wake": "w.wav", "ok": "o.wav"},
        follow_up=True, listen_timeout_s=0.05, clock=lambda: 0.0,
    )
    deps.update(over)
    c = ConversationController(**deps)
    c.spans = spans
    return c


def test_constructor_defaults_idle():
    c = make_controller()
    assert c.mode is Mode.IDLE
    assert c.phase is None
    assert c.response is None and c.watchdog is None
    assert c.hands_free is False and c.stop_after_response is False
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conversation'`

- [ ] **Step 3: 최소 구현** — `conversation.py`

```python
# conversation.py
"""대화 상태 머신 — main() 의 흩어진 상태를 단일 컨트롤러로 추출.

Mode (상호배타): IDLE · CONVERSING · TRANSLATE · MEETING.
모든 전환은 _teardown(현재) → 새 모드 설정 → _apply_tap 를 거친다.
협력자는 생성자 주입(DI). 자세한 계약은 플랜/스펙 참조.
"""
import asyncio
import enum
import time


class Mode(enum.Enum):
    IDLE = "idle"
    CONVERSING = "conversing"
    TRANSLATE = "translate"
    MEETING = "meeting"


class Phase(enum.Enum):
    LISTENING = "listening"
    RESPONDING = "responding"


class MeetingPhase(enum.Enum):
    SETUP = "setup"
    LIVE = "live"


class ConversationController:
    def __init__(self, *, mic, recognizer, player, web_pub=None,
                 log, set_status, speak, transcribe, translate_audio,
                 mode_intent, translate_mode, make_setup, make_meeting,
                 after_meeting_start, dispatch_command, fx,
                 follow_up=True, listen_timeout_s=8.0, clock=time.monotonic):
        self.mic = mic
        self.recognizer = recognizer
        self.player = player
        self.web_pub = web_pub
        self.log = log
        self.set_status = set_status
        self.speak = speak
        self.transcribe = transcribe
        self.translate_audio = translate_audio
        self.mode_intent = mode_intent
        self.translate_mode = translate_mode
        self.make_setup = make_setup
        self.make_meeting = make_meeting
        self.after_meeting_start = after_meeting_start
        self.dispatch_command = dispatch_command
        self.fx = fx
        self.follow_up = follow_up
        self.listen_timeout_s = listen_timeout_s
        self._clock = clock

        self.mode = Mode.IDLE
        self.phase = None
        self.meeting_phase = None
        self.meeting_session = None
        self.meeting_setup = None
        self.saved_mic_mode = None
        self.hands_free = False
        self.stop_after_response = False
        self.response = None
        self.watchdog = None
        self._output_busy_until = 0.0
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: 커밋**

```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): Mode/Phase enum + ConversationController 골격 + 테스트 fake"
```

---

## Task 2: 에코게이트·헬퍼·tap 파생 (`is_output_busy`, `mark_web_speaking`, `_apply_tap`, `_feed_recognizer`, `_cancel`)

**Files:**
- Modify: `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_conversation.py` 끝에

```python
def test_output_busy_and_mark_web_speaking():
    now = [100.0]
    c = make_controller(clock=lambda: now[0])
    assert c.is_output_busy() is False
    c.mark_web_speaking(2.0)            # 100 + 2 = 102 까지 busy
    assert c.is_output_busy() is True
    now[0] = 103.0
    assert c.is_output_busy() is False
    c.player.speaking = True            # 로컬 재생 중이면 busy
    assert c.is_output_busy() is True


def test_apply_tap_per_mode():
    c = make_controller()
    # CONVERSING·LISTENING → recognizer feed
    c.mode = Mode.CONVERSING; c.phase = Phase.LISTENING
    c._apply_tap()
    assert c.mic.tap == c._feed_recognizer
    # RESPONDING → None
    c.phase = Phase.RESPONDING; c._apply_tap()
    assert c.mic.tap is None
    # TRANSLATE → None
    c.mode = Mode.TRANSLATE; c.phase = None; c._apply_tap()
    assert c.mic.tap is None
    # MEETING·LIVE → session.feed
    sess = FakeSession()
    c.mode = Mode.MEETING; c.meeting_phase = MeetingPhase.LIVE; c.meeting_session = sess
    c._apply_tap()
    assert c.mic.tap == sess.feed_block


def test_feed_recognizer_echo_gate():
    now = [0.0]
    c = make_controller(clock=lambda: now[0])
    c.mark_web_speaking(1.0)             # busy until 1.0
    c._feed_recognizer(np.zeros(4, dtype=np.float32))
    assert c.recognizer.fed == []        # busy → drop
    now[0] = 2.0
    c._feed_recognizer(np.ones(4, dtype=np.float32))
    assert len(c.recognizer.fed) == 1    # not busy → feed
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `AttributeError: ... 'is_output_busy'`

- [ ] **Step 3: 구현** — `conversation.py` 의 `ConversationController` 에 메서드 추가

```python
    # --- 헬퍼 ---
    async def _cancel(self, task):
        """task 취소. 단, 현재 실행 중인 자기 자신은 건드리지 않는다(자기취소 데드락 방지)."""
        if task is None or task is asyncio.current_task() or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def is_output_busy(self):
        """로컬 스피커 재생 중이거나 웹 TTS 추정 재생 구간이면 True(에코게이트)."""
        return self.player.is_speaking() or self._clock() < self._output_busy_until

    def mark_web_speaking(self, dur):
        self._output_busy_until = max(self._output_busy_until, self._clock()) + dur

    def current_response(self):
        return self.response

    def in_meeting(self):
        return self.mode is Mode.MEETING

    def in_meeting_setup(self):
        return self.mode is Mode.MEETING and self.meeting_phase is MeetingPhase.SETUP

    def _apply_tap(self):
        """현재 모드/phase 에서 마이크 블록을 어디로 보낼지 한 곳에서 결정."""
        if (self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING
                and self.recognizer is not None):
            self.mic.set_tap(self._feed_recognizer)
        elif (self.mode is Mode.MEETING and self.meeting_phase is MeetingPhase.LIVE
                and self.meeting_session is not None):
            self.mic.set_tap(self.meeting_session.feed_block)
        else:
            self.mic.set_tap(None)

    def _feed_recognizer(self, block):
        if self.is_output_busy():
            return
        self.recognizer.feed_block(block)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): 에코게이트·tap 파생·취소 헬퍼"
```

---

## Task 3: `_teardown` + `_set_idle` + `_to_listening` (IDLE↔CONVERSING 전환)

**Files:**
- Modify: `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_set_idle_clears_tap_and_state():
    async def run():
        c = make_controller()
        c.mode = Mode.CONVERSING; c.phase = Phase.LISTENING; c._apply_tap()
        await c._set_idle()
        assert c.mode is Mode.IDLE and c.phase is None
        assert c.mic.tap is None
    asyncio.run(run())


def test_to_listening_sets_tap_cue_and_watchdog():
    async def run():
        c = make_controller()
        await c._to_listening(cue=True)
        assert c.mode is Mode.CONVERSING and c.phase is Phase.LISTENING
        assert c.mic.tap == c._feed_recognizer
        assert "w.wav" in c.player.files           # cue 재생
        assert c.watchdog is not None
        await c._cancel(c.watchdog)                # 정리
    asyncio.run(run())


def test_teardown_cancels_response_when_leaving_conversing():
    async def run():
        c = make_controller()
        c.mode = Mode.CONVERSING; c.phase = Phase.RESPONDING
        async def slow(): await asyncio.sleep(10)
        c.response = asyncio.create_task(slow())
        await c._set_idle()                        # teardown 이 response 취소
        assert c.response is None
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `AttributeError: ... '_set_idle'`

- [ ] **Step 3: 구현** — 메서드 추가

```python
    # --- 전환: teardown → 새 모드 ---
    async def _teardown(self):
        """현재 모드를 깨끗이 종료(자원 정리·복원). 새 모드 진입 전 항상 호출."""
        if self.mode is Mode.CONVERSING:
            await self._cancel(self.response); self.response = None
            await self._cancel(self.watchdog); self.watchdog = None
        elif self.mode is Mode.TRANSLATE:
            self.translate_mode.end_translate()
            await self._cancel(self.response); self.response = None
        elif self.mode is Mode.MEETING:
            if self.meeting_phase is MeetingPhase.LIVE and self.meeting_session is not None:
                try:
                    await self.meeting_session.stop()
                except Exception as e:
                    self.log(f"회의 종료 중 오류: {e}")
                self.mic.restore_mode(self.saved_mic_mode)   # 회의 전 소스 복원(불변식)
                self.set_status(None)
                if self.web_pub is not None:
                    self.web_pub.emit("navigate", "home")
            self.meeting_session = None
            self.meeting_setup = None
            self.saved_mic_mode = None
        self.mic.set_tap(None)

    async def _set_idle(self):
        await self._teardown()
        self.mode = Mode.IDLE
        self.phase = None
        self._apply_tap()
        self.log("\n🎙️  'Hey Jarvis' 라고 부르거나 아래에 텍스트를 입력하세요.")

    async def _to_listening(self, cue=True):
        await self._teardown()
        self.mode = Mode.CONVERSING
        self.phase = Phase.LISTENING
        self._apply_tap()
        if cue:
            await self.player.enqueue_file(self.fx["wake"])
        self.log("🔔 듣고 있어요…")
        self.watchdog = asyncio.create_task(self._listen_timeout())

    async def _listen_timeout(self):
        try:
            await asyncio.sleep(self.listen_timeout_s)
        except asyncio.CancelledError:
            return
        if self.hands_free:
            return
        if self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING:
            self.log("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
            await self._set_idle()
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): teardown→enter 전환 + IDLE/LISTENING + 타임아웃"
```

---

## Task 4: 응답 흐름 (`on_utterance` 음성, `_respond_audio`, `_after_response`)

**Files:**
- Modify: `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_on_utterance_conversing_runs_response_then_idle():
    async def run():
        c = make_controller()
        c.follow_up = False
        await c._to_listening(cue=False)
        await c.on_utterance(np.zeros(4, dtype=np.float32))
        assert c.phase is Phase.RESPONDING
        await c.response                       # 응답 완료 대기
        assert c.spans["speak"] == ["안녕"]    # transcribe→speak
        assert c.mode is Mode.IDLE             # follow_up=False → idle
    asyncio.run(run())


def test_after_response_follow_up_relistens():
    async def run():
        c = make_controller()           # follow_up=True (기본)
        await c._to_listening(cue=False)
        await c.on_utterance(np.zeros(4, dtype=np.float32))
        await c.response
        assert c.mode is Mode.CONVERSING and c.phase is Phase.LISTENING
        await c._cancel(c.watchdog)
    asyncio.run(run())


def test_stop_after_response_goes_idle_even_with_hands_free():
    async def run():
        c = make_controller()
        c.hands_free = True
        await c._to_listening(cue=False)
        c.stop_after_response = True
        await c.on_utterance(np.zeros(4, dtype=np.float32))
        await c.response
        assert c.mode is Mode.IDLE
        assert c.stop_after_response is False
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `AttributeError: ... 'on_utterance'`

- [ ] **Step 3: 구현** — 메서드 추가

```python
    # --- 발화 처리 ---
    async def on_speech_start(self):
        """VAD 발화 시작 — 듣는 중이면 무발화 타임아웃 취소."""
        if self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING:
            await self._cancel(self.watchdog); self.watchdog = None

    async def on_utterance(self, audio):
        """VAD 가 확정한 발화 블록."""
        if self.mode is Mode.TRANSLATE:
            asyncio.create_task(self.translate_audio(audio))
            return
        if self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING:
            await self._cancel(self.watchdog); self.watchdog = None
            self.phase = Phase.RESPONDING
            self._apply_tap()
            self.response = asyncio.create_task(self._respond_audio(audio))

    async def _respond_audio(self, audio):
        await self.player.enqueue_file(self.fx["ok"])
        self.set_status("받아쓰는 중…")
        try:
            text = await self.transcribe(audio)
        finally:
            self.set_status(None)
        await self._dispatch_response_text(text, from_voice=True)

    async def _dispatch_response_text(self, text, *, from_voice):
        if text:
            intent = self.mode_intent(text)
            if intent == "meeting":
                self._log_user(text)
                if self.web_pub is not None:
                    self.web_pub.emit("assistant", "🎤 회의 모드로 전환합니다")
                await self.start_meeting()
                return
            if intent == "stop":
                self._log_user(text)
                if self.in_meeting():
                    await self.stop_meeting()
                else:
                    await self.stop_translate()
                return
            await self.speak(text)
        else:
            self.log("🧑 (인식된 음성 없음)" if from_voice else "")
        await self._wait_output_done()
        await self._after_response()

    def _log_user(self, text):
        self.log(f"🧑 {text}")
        if self.web_pub is not None:
            self.web_pub.emit("user", text)

    async def _wait_output_done(self):
        while self.is_output_busy():
            await asyncio.sleep(0.1)

    async def _after_response(self):
        if self.stop_after_response:
            self.stop_after_response = False
            await self._set_idle()
        elif self.hands_free or self.follow_up:
            await self._to_listening(cue=True)
        else:
            await self._set_idle()
```

> 주의: `_dispatch_response_text` 의 `await ... if ... else await ...` 한 줄은 파이썬에서
> 유효한 조건식이지만 가독성을 위해 다음으로 풀어 써도 된다:
> `if self.in_meeting(): await self.stop_meeting()` / `else: await self.stop_translate()`.
> `stop_translate`/`start_meeting`/`stop_meeting` 은 Task 5~6 에서 정의된다. 이 태스크의
> 테스트는 intent=None 경로만 검증하므로 아직 그 메서드들이 없어도 통과한다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): 음성 응답 흐름 + after_response 분기"
```

---

## Task 5: 텍스트·STT콜백·웹청취 (`on_text`, `_respond_text`, `on_final`, `on_partial`, `start_listening`, `stop_listening`, `on_wake`, `request_stop`)

**Files:**
- Modify: `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_on_final_only_when_listening():
    async def run():
        c = make_controller()
        c.follow_up = False
        # 듣는 중 아님 → 무시
        c.mode = Mode.IDLE
        c.on_final("무시될 말")
        assert c.response is None
        # 듣는 중 → 응답
        await c._to_listening(cue=False)
        c.on_final("진짜 말")
        assert c.response is not None
        await c.response
        assert c.spans["speak"] == ["진짜 말"]
        assert c.mode is Mode.IDLE
    asyncio.run(run())


def test_start_listening_hands_free_and_stop_listening_idle():
    async def run():
        c = make_controller()
        await c.start_listening(hands_free=True)
        assert c.mode is Mode.CONVERSING and c.phase is Phase.LISTENING
        assert c.hands_free is True
        await c.stop_listening()               # 응답 중 아님 → 즉시 idle
        assert c.mode is Mode.IDLE and c.hands_free is False
        await c._cancel(c.watchdog)
    asyncio.run(run())


def test_stop_listening_during_response_sets_flag():
    async def run():
        c = make_controller()
        c.hands_free = True
        c.mode = Mode.CONVERSING; c.phase = Phase.RESPONDING
        async def slow(): await asyncio.sleep(10)
        c.response = asyncio.create_task(slow())
        await c.stop_listening()
        assert c.stop_after_response is True
        assert c.mode is Mode.CONVERSING       # 응답은 계속
        await c._cancel(c.response)
    asyncio.run(run())


def test_on_text_command_handled_no_idle():
    async def run():
        c = make_controller()
        c.spans["handled"] = True              # dispatch_command → True
        c.mode = Mode.CONVERSING; c.phase = Phase.LISTENING
        await c.on_text("/mic")
        await c.response
        # 명령이 상태 점유 → idle 강제 안 함(여기선 LISTENING 유지)
        assert c.mode is Mode.CONVERSING
        await c._cancel(c.watchdog)
    asyncio.run(run())


def test_on_text_plain_speaks_then_idle():
    async def run():
        c = make_controller()
        c.follow_up = False
        await c.on_text("오늘 날씨")
        await c.response
        assert c.spans["speak"] == ["오늘 날씨"]
        assert c.mode is Mode.IDLE
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `AttributeError: ... 'on_final'`

- [ ] **Step 3: 구현** — 메서드 추가

```python
    # --- 스트리밍 STT 콜백(이벤트 루프에서 호출됨) ---
    def on_partial(self, text):
        self.set_status(f"📝 {text[:80]}")
        if self.web_pub is not None:
            self.web_pub.emit("partial", text)

    def on_final(self, text):
        if not (self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING):
            return   # stray final 무시
        self.stop_after_response = False
        self.phase = Phase.RESPONDING
        self._apply_tap()
        self.response = asyncio.create_task(self._respond_after_final((text or "").strip()))

    async def _respond_after_final(self, text):
        await self._cancel(self.watchdog); self.watchdog = None
        await self._dispatch_response_text(text, from_voice=True)

    # --- 웹 청취 토글 ---
    async def start_listening(self, hands_free):
        self.hands_free = hands_free
        await self.on_wake()

    async def stop_listening(self):
        self.hands_free = False
        if self.response is not None and not self.response.done():
            self.stop_after_response = True   # 응답 끝까지 두고 끝나면 idle
        else:
            await self._set_idle()

    async def on_wake(self):
        """호출어 / '/mic' — 응답 중단 + 듣기 시작. TRANSLATE·MEETING 에선 무시."""
        if self.mode in (Mode.TRANSLATE, Mode.MEETING):
            return
        self.player.flush()
        await self._to_listening(cue=True)

    async def request_stop(self):
        """Esc — 진행 중 응답 취소 후 대기 복귀(대화 모드에 한함)."""
        if self.mode is Mode.CONVERSING and self.response is not None \
                and not self.response.done():
            self.player.flush()
            self.set_status(None)
            self.log("⏹  진행 중 응답을 멈췄어요.")
            await self._set_idle()

    # --- 텍스트 입력 ---
    async def on_text(self, line):
        if self.in_meeting_setup():
            await self._handle_setup_input(line)
            return
        self.response = asyncio.create_task(self._respond_text(line))

    async def _respond_text(self, line):
        handled = await self.dispatch_command(line)
        if handled:
            return
        await self._dispatch_response_text(line, from_voice=False)
```

> `_handle_setup_input` 은 Task 6(회의)에서 정의. 이 태스크 테스트는 setup 경로를 타지 않음.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (15 passed)

- [ ] **Step 5: 커밋**

```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): 텍스트·STT콜백·웹청취·on_wake·Esc"
```

---

## Task 6: TRANSLATE + MEETING 모드 (`start_translate`, `stop_translate`, `start_meeting`, `stop_meeting`, `_handle_setup_input`)

**Files:**
- Modify: `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_translate_mode_enter_exit():
    async def run():
        c = make_controller()
        await c.start_translate("en")
        assert c.mode is Mode.TRANSLATE
        assert c.translate_mode.is_translate() is True
        assert c.mic.tap is None
        # 번역 모드 발화 → translate_audio 호출, 모드 유지
        await c.on_utterance(np.zeros(4, dtype=np.float32))
        await asyncio.sleep(0)   # 백그라운드 태스크 진입
        assert c.mode is Mode.TRANSLATE
        # on_wake 무시
        await c.on_wake()
        assert c.mode is Mode.TRANSLATE
        await c.stop_translate()
        assert c.mode is Mode.IDLE
        assert c.translate_mode.is_translate() is False
    asyncio.run(run())


def test_meeting_enter_snapshots_and_live():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)
        await c.start_meeting()
        assert c.mode is Mode.MEETING and c.meeting_phase is MeetingPhase.LIVE
        assert sess.started is True
        assert c.mic.snapshots == 1            # 진입 시 소스 모드 저장
        assert c.mic.tap == sess.feed_block    # tap = 회의 STT
        assert ("navigate", "meeting") in c.web_pub.emits
        assert sess in c.spans["after"]        # after_meeting_start 훅 호출
    asyncio.run(run())


def test_meeting_exit_restores_mic_source():
    """회귀: 회의 종료 후 mic 모드가 입장 전으로 복원된다."""
    async def run():
        sess = FakeSession()
        mic = FakeMic(); mic._mode = "remote"   # 입장 전 폰 소스
        c = make_controller(mic=mic, make_meeting=lambda meta: sess)
        await c.start_meeting()
        await c.stop_meeting()
        assert c.mode is Mode.IDLE
        assert sess.stopped is True
        assert mic.restored == ["remote"]       # snapshot 값으로 복원
        assert ("navigate", "home") in c.web_pub.emits
    asyncio.run(run())


def test_meeting_setup_two_phase_then_input():
    async def run():
        setup = FakeSetup(done=False)
        sess = FakeSession()
        c = make_controller(make_setup=lambda: setup, make_meeting=lambda meta: sess)
        await c.start_meeting()
        assert c.mode is Mode.MEETING and c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("상대이름")             # setup 입력 → done → LIVE
        assert c.meeting_phase is MeetingPhase.LIVE
        assert sess.started is True
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `AttributeError: ... 'start_translate'`

- [ ] **Step 3: 구현** — 메서드 추가

```python
    # --- TRANSLATE ---
    async def start_translate(self, src_lang):
        await self._teardown()
        self.translate_mode.start_translate(src_lang)
        self.mode = Mode.TRANSLATE
        self.phase = None
        self._apply_tap()
        suffix = f" (입력 언어: {src_lang})" if src_lang else " (입력 언어 자동 감지)"
        self.log(f"🌐 번역 모드 시작{suffix}. 끝내려면 /stop.")

    async def stop_translate(self):
        if self.mode is not Mode.TRANSLATE:
            self.log("번역 모드가 아닙니다.")
            return
        await self._set_idle()
        self.log("🌐 번역 모드 종료.")

    # --- MEETING ---
    async def start_meeting(self):
        if self.mode is Mode.MEETING:
            self.log("회의 모드가 이미 진행 중입니다.")
            return
        await self._teardown()
        setup = self.make_setup()
        self.mode = Mode.MEETING
        if not setup.done:
            self.meeting_phase = MeetingPhase.SETUP
            self.meeting_setup = setup
            self._apply_tap()
            self.log(f"🎤 회의 시작 전 정보를 입력해주세요. (Esc 로 취소)")
            self.log(f"   {setup.prompt}")
            return
        await self._begin_meeting(setup.meta)

    async def _begin_meeting(self, meta):
        try:
            sess = self.make_meeting(meta)
            await sess.start()
        except Exception as e:
            self.log(f"회의 모드 시작 실패: {e}")
            await self._set_idle()
            return
        self.meeting_session = sess
        self.meeting_setup = None
        self.meeting_phase = MeetingPhase.LIVE
        self.saved_mic_mode = self.mic.snapshot_mode()   # 종료 시 복원할 소스
        self._apply_tap()                                # tap = sess.feed_block
        if self.web_pub is not None:
            self.web_pub.emit("navigate", "meeting")
        self.after_meeting_start(sess)                   # web listener + 자막 URL 로그

    async def stop_meeting(self):
        if self.mode is not Mode.MEETING:
            self.log("회의 모드가 아닙니다.")
            return
        await self._set_idle()
        self.log("🎤 회의 모드 종료.")

    async def _handle_setup_input(self, line):
        setup = self.meeting_setup
        if setup is None:
            return
        stripped = line.strip()
        if stripped.lower() in ("/stop", "/cancel", "취소"):
            self.meeting_setup = None
            await self._set_idle()
            self.log("🎤 회의 시작을 취소했어요.")
            return
        if not stripped:
            self.log(f"   {setup.prompt}")
            return
        setup.submit(stripped)
        if not setup.done:
            self.log(f"   {setup.prompt}")
            return
        await self._begin_meeting(setup.meta)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (19 passed)

- [ ] **Step 5: 전체 회귀 확인 + 커밋**

```bash
.venv/bin/python -m pytest -q          # 기존 71 + 신규 전부 통과 확인
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): TRANSLATE/MEETING 모드 + 회의 소스 복원 회귀테스트"
```

Expected: 90 passed (71 기존 + 19 신규)

---

## Task 7: main() 배선 — 컨트롤러 생성 + speak_response 의 echo gate 위임

**Files:**
- Modify: `main.py` (web_speaking_until 사용처 / speak_response / 컨트롤러 생성)

**배경:** 현재 `main.py` 의 상태 함수들(`idle`, `enter_listening`, `trigger_wake`,
`respond_flow_*`, `listen_timeout`, 회의/번역 함수, `_on_stt_*`, `on_escape`)을
컨트롤러로 대체한다. 이 태스크는 **컨트롤러 인스턴스를 만들고 speak_response 만
연결**한다(아직 옛 함수 제거 X — 다음 태스크에서 호출부를 옮긴 뒤 제거).

- [ ] **Step 1: speak_response 가 컨트롤러 시계를 쓰도록 변경**

`main.py` 의 `speak_response`(현재 191~220) 에서 `nonlocal web_speaking_until` 과
`web_speaking_until = max(...) + dur` 줄을 컨트롤러 호출로 교체. 컨트롤러는 Step 2 에서
`controller` 이름으로 생성되므로, speak_response 정의를 컨트롤러 생성 **이후**로
옮기거나, 클로저가 늦게 바인딩되는 점을 이용(함수 본문은 호출 시점에 `controller`
참조 — 생성이 먼저면 안전). 본 플랜은 **speak_response 를 컨트롤러 생성 이후로 이동**한다.

변경 후 해당 라인:

```python
                if web_pub is not None and web_pub.web_viewer_count > 0:
                    pcm16 = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                    web_pub.emit_audio(pcm16, sr)
                    dur = len(wav) / float(sr)
                    controller.mark_web_speaking(dur)
                else:
                    await player.enqueue(wav, sr)
```

`nonlocal web_speaking_until` 줄은 삭제.

- [ ] **Step 2: 컨트롤러 생성 (recognizer 초기화 직후, idle() 호출 직전 — 현재 638 부근)**

`recognizer` 초기화 블록(626~635) 다음, `console.set_escape_handler(on_escape)`
(637) 앞에 삽입:

```python
    from conversation import ConversationController

    async def _translate_audio(audio):
        await _translate_bg(audio)

    def _after_meeting_start(sess):
        # 원본 _begin_meeting 의 로그/리스너 등록 재현
        console.log(f"🎤 회의를 시작합니다. 회의 번호: {sess.meta.key}")
        if web_pub is not None:
            sess.add_listener(web_pub.emit_async)
            view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
            console.log(f"🌐 자막: {view_base}/{sess.meta.key}/meeting")
            # 전제: MeetingSession 이 생성자에서 받은 meta 를 self.meta 로 보관(아래 비고 참조)

    def _make_meeting(meta):
        from live_translate import MeetingSession
        return MeetingSession(
            log=console.log, set_status=console.set_status, llm=llm, meta=meta,
            model=config.MEET_STT_MODEL, realtime_model=config.MEET_STT_REALTIME_MODEL,
        )

    def _make_setup():
        from live_translate import MeetingSetup
        return MeetingSetup(default_my_name=config.USER_NAME)

    async def _dispatch_command(line):
        cmd_ctx["handled_state"] = False
        if commands.is_command(line):
            await commands.dispatch(line, cmd_ctx)
            return bool(cmd_ctx.get("handled_state"))
        return False

    controller = ConversationController(
        mic=mic.router, recognizer=recognizer, player=player, web_pub=web_pub,
        log=console.log, set_status=console.set_status,
        speak=speak_response, transcribe=stt.transcribe, translate_audio=_translate_audio,
        mode_intent=mode_intent, translate_mode=MODE,
        make_setup=_make_setup, make_meeting=_make_meeting,
        after_meeting_start=_after_meeting_start, dispatch_command=_dispatch_command,
        drain_queue=_drain_text_queue,
        fx={"wake": config.FX_WAKE, "ok": config.FX_OK},
        follow_up=config.FOLLOW_UP, listen_timeout_s=config.LISTEN_TIMEOUT_S,
    )
```

> `drain_queue=_drain_text_queue`: 컨트롤러가 wake/translate/meeting 진입 시 대기 텍스트
> 입력을 비우도록(원본 `trigger_wake`/`start_translate`/`start_meeting_setup` 의 `_drain_text_queue()`
> 동작 보존). `_drain_text_queue` 는 main 에 이미 존재(330~340).

> 자막 URL 은 `sess.meta.key` 를 쓴다. `MeetingSession` 이 생성자에서 받은 meta 를
> `self.meta` 로 보관하는지 구현에서 확인하고, 없으면 `live_translate.py` 의
> `MeetingSession.__init__` 에 `self.meta = meta` 한 줄을 추가한다(이 태스크의 Step 5
> 커밋에 포함). 기존 `_begin_meeting`(현재 466~484)이 `meta.key` 를 동일하게 썼으므로
> 보관돼 있을 가능성이 높다.

- [ ] **Step 3: import 추가 확인** — `main.py` 상단에 `mode_intent` import 가 있는지 확인,
없으면 추가: `from intent import mode_intent` (이미 respond 흐름에서 쓰므로 존재할 것).

Run: `.venv/bin/python -c "import main; print('import ok')"`
Expected: `import ok` (아직 옛 함수와 공존 — 컴파일만 확인)

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `.venv/bin/python -m pytest -q`
Expected: 90 passed (동작 변경 없음 — 컨트롤러는 아직 미사용)

- [ ] **Step 5: 커밋**

```bash
git add main.py live_translate.py
git commit -m "wire(main): ConversationController 생성 + speak_response echo gate 위임"
```

---

## Task 8: main() 루프·콜백을 컨트롤러 위임으로 교체 + 옛 상태 함수 제거

**Files:**
- Modify: `main.py`

**배경:** 이제 호출부를 컨트롤러로 옮기고, `state/response/watchdog/hands_free/
stop_after_response/web_speaking_until` nonlocal 과 옛 함수들(`idle, enter_listening,
trigger_wake, respond_flow_audio, respond_flow_text, listen_timeout, _handle_mode,
start_translate, stop_translate, start_meeting_setup, _begin_meeting, stop_meeting,
cancel_meeting_setup, handle_meeting_setup_input, in_meeting_setup, _on_stt_partial,
_on_stt_final, _recognizer_feed, on_escape, _cancel_response_and_idle`)을 제거한다.
`_translate_bg`, `_drain_text_queue`, `_refresh_queue_display`, `_snapshot_queue`,
`speak_response`, `cancel` 은 유지.

- [ ] **Step 1: audio_loop 교체** (현재 520~539)

```python
    async def audio_loop():
        """마이크 이벤트 소비 → 컨트롤러 위임."""
        async for kind, audio in mic.events(
            wake_detect=wake.detect,
            is_speaking=lambda: player.is_speaking() or controller.is_output_busy(),
        ):
            if kind == "wake":
                if MODE.is_translate():
                    continue
                await controller.on_wake()
            elif kind == "start":
                await controller.on_speech_start()
            elif kind == "utterance":
                await controller.on_utterance(audio)
```

- [ ] **Step 2: text_worker 교체** (현재 555~577)

```python
    async def text_worker():
        while True:
            r = controller.current_response()
            if r is not None and not r.done():
                try:
                    await r
                except (asyncio.CancelledError, Exception):
                    pass
            line = await text_queue.get()
            _refresh_queue_display()
            await controller.on_text(line)
```

- [ ] **Step 3: 콜백 어댑터 교체**

`_on_remote_command`(101~131) 본문의 분기를 컨트롤러 호출로:

```python
        async def _on_remote_command(msg):
            kind = msg.get("kind")
            if kind == "meeting_stop":
                await controller.stop_meeting()
            elif kind == "meeting_start":
                await controller.start_meeting()
            elif kind == "mic_system":
                mic.router.set_override("local")
            elif kind == "mic_phone":
                mic.router.set_override("remote")
            elif kind == "listen_start":
                await controller.start_listening(hands_free=True)
            elif kind == "listen_stop":
                await controller.stop_listening()
            elif kind == "get_settings":
                if web_pub is not None:
                    web_pub.emit("settings", json.dumps(settings.current()))
            elif kind == "apply_settings":
                settings.apply(msg.get("value") or {})
                console.log(f"⚙️ 설정 변경: {settings.current()}")
                if web_pub is not None:
                    web_pub.emit("settings", json.dumps(settings.current()))
```

`recognizer` 콜백을 컨트롤러로: 초기화(626~631)의 `on_partial=_on_stt_partial,
on_final=_on_stt_final` 을 `on_partial=controller.on_partial,
on_final=controller.on_final` 로 변경. **단** 컨트롤러가 recognizer 보다 먼저 생성돼야
하므로, 순서를 조정한다 — recognizer 생성 시 콜백으로 `controller.on_partial/on_final`
을 넘기려면 controller 가 먼저 있어야 하고, controller 는 recognizer 를 인자로 받는다
(상호참조). 해결: recognizer 를 먼저 만들되 콜백은 **얇은 람다**로 위임:

```python
        recognizer = StreamingRecognizer(
            on_partial=lambda t: controller.on_partial(t),
            on_final=lambda t: controller.on_final(t),
            model=config.MEET_STT_MODEL, realtime_model=config.MEET_STT_REALTIME_MODEL,
            language=config.WHISPER_LANG, on_log=console.log,
        )
```

람다는 호출 시점에 `controller` 를 참조하므로 생성 순서(recognizer→controller)와 무관.

`on_escape` 교체(342~367 삭제 후):

```python
    def on_escape():
        if controller.in_meeting_setup():
            asyncio.create_task(controller._handle_setup_input("/cancel"))
            return
        if not text_queue.empty():
            _drain_text_queue()
            return
        asyncio.create_task(controller.request_stop())
```

- [ ] **Step 4: cmd_ctx 를 컨트롤러 메서드로 연결** (507~512 부근)

```python
    cmd_ctx["trigger_wake"] = controller.on_wake
    cmd_ctx["start_translate"] = controller.start_translate
    cmd_ctx["stop_translate"] = controller.stop_translate
    cmd_ctx["start_meeting"] = controller.start_meeting
    cmd_ctx["stop_meeting"] = controller.stop_meeting
    cmd_ctx["in_meeting"] = controller.in_meeting
```

(`commands.py` 는 무변경 — 동일 키·시그니처 사용.)

- [ ] **Step 5: 초기 idle + 옛 함수/nonlocal 제거**

- `idle()`(638) 호출을 `await controller._set_idle()` 로 (단, main 본체는 async 이므로 가능).
  또는 컨트롤러는 기본 IDLE 이므로 안내 로그만 필요 — `await controller._set_idle()` 사용.
- Step 8 배경에 나열한 옛 함수들과 `state, response, watchdog, hands_free,
  stop_after_response, web_speaking_until, web_speaking_until = 0.0`(60),
  `recognizer = None`(160) 의 불필요해진 nonlocal 선언/함수를 모두 삭제.
- `finally` 정리부(655~)의 `await cancel(response)`, `await cancel(watchdog)` 는
  `await controller._cancel(controller.response)`, `await controller._cancel(controller.watchdog)`
  로 교체.

- [ ] **Step 6: 컴파일 + 전체 테스트**

Run: `.venv/bin/python -c "import main; print('import ok')" && .venv/bin/python -m pytest -q`
Expected: `import ok` + 90 passed

- [ ] **Step 7: 커밋**

```bash
git add main.py
git commit -m "refactor(main): 대화 상태를 ConversationController 로 위임, 옛 nonlocal/함수 제거"
```

---

## Task 9: 수동 E2E 검증 + 정리

**Files:** (변경 없음 — 검증 단계)

- [ ] **Step 1: 정적 점검** — main.py 에 남은 옛 상태 심볼이 없는지 확인

Run:
```bash
grep -n "web_speaking_until\|nonlocal state\|def enter_listening\|def respond_flow\|def trigger_wake\|def _on_stt_final" main.py || echo "정리 완료"
```
Expected: `정리 완료` (매치 없음)

- [ ] **Step 2: 전체 테스트 + import**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -c "import main"`
Expected: 90 passed, 에러 없음

- [ ] **Step 3: 수동 E2E 체크리스트 (jarvis 재시작 후 사용자 확인)**

다음을 사용자에게 실행 요청:
- 일반 음성: 'Hey Jarvis' → 질문 → 응답 → 대기 복귀
- 팔로업: 응답 후 호출어 없이 재청취(FOLLOW_UP=true)
- hands_free: 웹 음성버튼 ON→대화→OFF (응답 중 OFF 시 응답 끝나고 대기)
- 번역: `/trans` → 발화 번역 → `/stop`
- 회의: `/meet` 또는 웹 +메뉴 → 자막 → 종료
- **회의→일반 mic 소스**: 회의 입·퇴장 후 웹/폰 음성이 정상 인식되는지
- Esc: 응답 중 Esc → 즉시 중단·대기 복귀

- [ ] **Step 4: 최종 커밋(있다면)** — 정리/주석만

```bash
git add -A && git commit -m "chore(conversation): 리팩터 정리" || echo "변경 없음"
```

---

## 비고

- **commands.py**: cmd_ctx 키·시그니처 동일 → 무변경 목표. 만약 `mic_router` 키가
  필요하면 기존대로 유지(149~158 의 cmd_ctx 생성부는 그대로 둠).
- **live_translate.py**: `MeetingSession.meta` 보관이 없으면 Task 7 Step 2 비고대로
  `self.meta = meta` 한 줄 추가(자막 URL 용). 그 외 무변경.
- **동작 변경(의도)**: 자기취소 방지(`_cancel` current_task 가드), 전환 시 항상
  teardown, 회의 종료 시 소스 복원 패턴화 — 스펙 "의도된 동작 변경" 절 참고.
