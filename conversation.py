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
