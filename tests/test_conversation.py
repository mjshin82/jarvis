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
