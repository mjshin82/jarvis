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
