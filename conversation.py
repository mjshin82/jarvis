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
                 drain_queue=lambda: None,
                 persist_mode=lambda m: None,
                 save_meeting=lambda record: None,
                 follow_up=True, listen_timeout_s=8.0, hands_free_timeout_s=30.0,
                 clock=time.monotonic):
        self.mic = mic
        self.recognizer = recognizer
        self.player = player
        self.web_pub = web_pub
        self.persist_mode = persist_mode
        self.save_meeting = save_meeting
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
        self.drain_queue = drain_queue
        self.fx = fx
        self.follow_up = follow_up
        self.listen_timeout_s = listen_timeout_s
        self.hands_free_timeout_s = hands_free_timeout_s
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
                try:
                    self.save_meeting(self.meeting_session.record())
                except Exception as e:
                    self.log(f"회의 기록 저장 실패: {e}")
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
        self.persist_mode("idle")
        self._apply_tap()
        if self.recognizer is not None:
            await self.recognizer.suspend()
        self.log("\n🎙️  'Hey Jarvis' 라고 부르거나 아래에 텍스트를 입력하세요.")

    async def _to_listening(self, cue=True):
        await self._teardown()
        self.mode = Mode.CONVERSING
        self.phase = Phase.LISTENING
        self.persist_mode("idle")
        if self.recognizer is not None:
            await self.recognizer.resume()
        self._apply_tap()
        if cue:
            await self.player.enqueue_file(self.fx["wake"])
        self.log("🔔 듣고 있어요…")
        self.watchdog = asyncio.create_task(self._listen_timeout())

    async def _listen_timeout(self):
        timeout = self.hands_free_timeout_s if self.hands_free else self.listen_timeout_s
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        if not (self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING):
            return
        if self.hands_free and self.web_pub is not None:
            self.web_pub.emit("mic_release")     # 웹에 마이크 해제 신호(얼럿 없이 버튼만)
        self.log("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
        await self._set_idle()

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
                await self.stop_meeting()
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
        self.drain_queue()
        self.player.flush()
        await self._to_listening(cue=True)

    async def request_stop(self):
        """Esc — 진행 중 응답이 있으면 취소 후 대기 복귀(상태 무관)."""
        if self.response is not None and not self.response.done():
            self.player.flush()
            self.set_status(None)
            self.log("⏹  진행 중 응답을 멈췄어요.")
            await self._cancel(self.response)
            self.response = None
            await self._set_idle()

    # --- 텍스트 입력 ---
    async def on_text(self, line):
        if self.in_meeting_setup():
            await self._handle_setup_input(line)
            return
        await self._cancel(self.watchdog); self.watchdog = None
        self.response = asyncio.create_task(self._respond_text(line))

    async def _respond_text(self, line):
        result = await self.dispatch_command(line)
        if result is None:
            await self._dispatch_response_text(line, from_voice=False)
        elif result is False:
            await self._wait_output_done()
            await self._set_idle()
        # result is True → 명령이 상태 점유, 아무 것도 안 함

    # --- TRANSLATE ---
    async def start_translate(self, src_lang):
        await self._teardown()
        self.player.flush()
        self.drain_queue()
        self.translate_mode.start_translate(src_lang)
        self.mode = Mode.TRANSLATE
        self.phase = None
        self.persist_mode("translate")
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
    async def start_meeting(self, meta=None, interactive=False):
        if self.mode is Mode.MEETING:
            self.log("회의 모드가 이미 진행 중입니다.")
            return
        await self._teardown()
        self.player.flush()
        self.drain_queue()
        if meta is not None:                       # 웹 폼/직접 메타 → 즉시 시작
            self.mode = Mode.MEETING
            await self._begin_meeting(meta)
            return
        setup = self.make_setup()
        self.mode = Mode.MEETING
        if interactive and not setup.done:         # 콘솔 /meet → 프롬프트
            self.meeting_phase = MeetingPhase.SETUP
            self.meeting_setup = setup
            self._apply_tap()
            self.log("🎤 회의 설정 — 항목을 입력하세요. (Esc 로 취소)")
            self.log(f"   {setup.prompt}")
            return
        await self._begin_meeting(setup.meta)      # 음성/복구 → 기본값 즉시

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
        self.persist_mode("meeting")
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
        setup.submit(stripped)   # 빈 입력은 해당 단계 기본값 수락(Enter=기본).
        if not setup.done:
            self.log(f"   {setup.prompt}")
            return
        await self._begin_meeting(setup.meta)
