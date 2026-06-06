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
