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
    async def resume(self): pass
    async def suspend(self): pass
    async def aclose(self): pass

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
    def record(self): return {"id": "fake", "transcript": []}

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
    async def dispatch_command(line): return spans.get("handled", None)
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
        persist_mode=lambda m: spans.setdefault("persist", []).append(m),
        dispatch_command=dispatch_command, fx={"wake": "w.wav", "ok": "o.wav"},
        follow_up=True, listen_timeout_s=0.05, hands_free_timeout_s=30.0, clock=lambda: 0.0,
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
        await c.start_meeting(interactive=True)
        assert c.mode is Mode.MEETING and c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("상대이름")             # setup 입력 → done → LIVE
        assert c.meeting_phase is MeetingPhase.LIVE
        assert sess.started is True
    asyncio.run(run())


def test_meeting_setup_empty_accepts_defaults():
    """회귀: 회의 설정 단계에서 빈 Enter(Enter=기본)가 단계를 넘기고 기본값으로 시작한다."""
    from live_translate import MeetingSetup
    async def run():
        sess = FakeSession()
        setup = MeetingSetup(default_my_name="민준")
        c = make_controller(make_setup=lambda: setup, make_meeting=lambda meta: sess)
        await c.start_meeting(interactive=True)
        assert c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("")                      # title 단계 Enter=기본
        assert c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("")                      # languages 단계 Enter=기본
        assert c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("")                      # vocabulary 단계 Enter=기본
        assert c.meeting_phase is MeetingPhase.SETUP   # 아직 password 단계
        await c.on_text("")                      # password 단계 → 시작
        assert c.meeting_phase is MeetingPhase.LIVE
        assert sess.started is True
        assert setup.meta.title == "회의"
        assert setup.meta.vocabulary == ["Jarvis", "민준"]
    asyncio.run(run())


def test_request_stop_cancels_text_response_in_idle():
    async def run():
        c = make_controller()
        # IDLE 에서 텍스트 응답이 도는 상황 모사
        async def slow(): await asyncio.sleep(10)
        c.response = asyncio.create_task(slow())
        await c.request_stop()
        assert c.response is None or c.response.cancelled() or c.response.done()
        assert c.mode is Mode.IDLE
    asyncio.run(run())


def test_stop_intent_calls_stop_meeting(monkeypatch=None):
    async def run():
        c = make_controller()
        c.spans["intent"] = "stop"
        c.spans["stt"] = "회의 종료"
        calls = []
        async def fake_stop(): calls.append("stop_meeting")
        c.stop_meeting = fake_stop
        await c._dispatch_response_text("회의 종료", from_voice=True)
        assert calls == ["stop_meeting"]
    asyncio.run(run())


def test_drain_queue_called_on_wake():
    async def run():
        drained = []
        c = make_controller(drain_queue=lambda: drained.append(1))
        await c.on_wake()
        assert drained == [1]
        await c._cancel(c.watchdog)
    asyncio.run(run())


def test_on_text_command_ran_but_not_state_idles_without_speak():
    async def run():
        c = make_controller()
        c.spans["handled"] = False   # 명령 실행됨, 상태 미점유 → idle, LLM 호출 X
        await c.on_text("/tts 안녕")
        await c.response
        assert "speak" not in c.spans
        assert c.mode is Mode.IDLE
    asyncio.run(run())


def test_on_text_cancels_listen_watchdog():
    async def run():
        c = make_controller()
        c.follow_up = False
        await c._to_listening(cue=False)   # watchdog armed
        assert c.watchdog is not None
        await c.on_text("질문")
        assert c.watchdog is None          # 타이핑 시 listen watchdog 취소
        await c.response
    asyncio.run(run())


def test_hands_free_timeout_releases_web_mic_and_idles():
    async def run():
        c = make_controller(hands_free_timeout_s=0.02, listen_timeout_s=0.02)
        await c.start_listening(hands_free=True)   # → CONVERSING/LISTENING, watchdog armed
        await asyncio.sleep(0.06)                  # watchdog 발화 대기
        assert ("mic_release", "") in c.web_pub.emits
        assert c.mode is Mode.IDLE
    asyncio.run(run())


def test_non_handsfree_timeout_idles_without_mic_release():
    async def run():
        c = make_controller(hands_free_timeout_s=10.0, listen_timeout_s=0.02)
        await c._to_listening(cue=False)           # hands_free=False
        await asyncio.sleep(0.06)
        assert c.mode is Mode.IDLE
        assert ("mic_release", "") not in c.web_pub.emits
    asyncio.run(run())


def test_persist_mode_idle_and_translate():
    async def run():
        c = make_controller()
        await c._set_idle()
        assert "idle" in c.spans.get("persist", [])
        await c.start_translate("en")
        assert c.spans["persist"][-1] == "translate"
    asyncio.run(run())


def test_persist_mode_meeting_on_begin():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)
        await c.start_meeting()
        assert "meeting" in c.spans.get("persist", [])
    asyncio.run(run())


def test_start_meeting_with_meta_skips_setup():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)
        await c.start_meeting(meta="DIRECT")     # 메타 직접 → 즉시 LIVE
        assert c.mode is Mode.MEETING and c.meeting_phase is MeetingPhase.LIVE
        assert sess.started is True
    asyncio.run(run())


def test_start_meeting_default_no_prompt():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)   # FakeSetup done=True
        await c.start_meeting()                  # interactive=False → 기본값 즉시 시작
        assert c.meeting_phase is MeetingPhase.LIVE
    asyncio.run(run())


def test_meeting_end_saves_record():
    async def run():
        saved = []
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess,
                            save_meeting=lambda r: saved.append(r))
        await c.start_meeting()
        await c.stop_meeting()
        assert saved == [{"id": "fake", "transcript": []}]
    asyncio.run(run())
