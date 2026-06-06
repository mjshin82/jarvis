"""Jarvis — 호출어 기반 로컬 음성 비서 오케스트레이터.

상태머신(음성 흐름):
  WAITING_WAKE  'Hey Jarvis' 대기 (다른 소리·에코는 모두 무시)
       │ 호출 감지 → wake.wav
       ▼
  LISTENING     사용자 발화 캡처(VAD). 무발화 LISTEN_TIMEOUT_S 초 → 대기 복귀
       │ 발화 끝 → ok.wav
       ▼
  RESPONDING    STT → LLM(스트리밍) → TTS 재생
       │ 재생 중 'Hey Jarvis' → 재생 중단 + 텍스트 큐 비움 → 듣기
       │ 재생 중 Esc → 재생 중단 + 텍스트 큐 비움 → 대기 복귀
       │ 재생 완료 → 텍스트 큐가 비어있으면 WAITING_WAKE, 있으면 다음 처리

콘솔 입력: 화면 하단의 '> ' 프롬프트에 텍스트를 치면 호출어 없이도 곧장
LLM 으로 흘러간다(STT 만 건너뛰고 이후 파이프라인은 동일). 진행 중 응답이
있어도 끊지 않고 큐에 쌓아두고 차례로 처리한다. 명시적으로 끊으려면 Esc.

이 구조가 에코 루프를 막는다: RESPONDING 중 마이크로 들어온 에코는
utterance 가 돼도 무시되고, 오직 진짜 호출어/텍스트 입력만 상태를 전환한다.
"""
import asyncio
import os

import numpy as np

import config
import console
import commands
import json
import settings
import coach
import runtime_state
from mode_intent import mode_intent
from audio_io import Microphone
from audio_backend import make_backend
from stt import STT
from llm import LLM
from meeting_store import MeetingStore
from tts import TTS
from player import Player
from wake import WakeWord
from simulation import MODE


async def main():
    mic = Microphone()
    stt = STT()
    llm = LLM()
    tts = TTS()
    backend = make_backend()
    await backend.start()
    player = Player(backend)
    wake = WakeWord()

    await llm.warmup()
    console.start()                                 # 콘솔 입력 활성화 (하단 프롬프트)
    player_task = asyncio.create_task(player.run())
    # 상시 웹 퍼블리셔 (대화/TTS/회의자막 공용). RELAY 설정 시 항상 연결.
    settings.load()            # setting.yaml 로드(없으면 기본값으로 생성)
    web_pub = None
    if config.RELAY_URL and config.RELAY_TOKEN:
        from relay_client import RelayClient
        from live_translate import MeetingMeta
        web_pub = RelayClient(
            config.RELAY_URL, config.RELAY_TOKEN, MeetingMeta(my_name=config.USER_NAME),
            on_log=console.log, connect_timeout=config.RELAY_TIMEOUT_S,
        )
        await web_pub.connect()
        web_pub.emit("settings", json.dumps(settings.current()))   # 초기 스냅샷(replay 로 늦은 owner 도 받음)
        home_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
        home_url = f"{home_base}/{config.ROOM_KEY}"
        bw = max(len(home_url) + 4, 60); border = "─" * bw
        console.log(""); console.log(f"┌{border}┐")
        console.log(f"│  🤖 Jarvis 웹 (로그인 후 대화/마이크)".ljust(bw + 1) + "│")
        console.log(f"│  {home_url}".ljust(bw + 1) + "│")
        console.log(f"└{border}┘"); console.log("")
    # 원격 마이크 (옵션): 웹 프론트가 보내는 외부 마이크 스트림을 relay 역방향으로 수신.
    remote_mic_rx = None
    remote_mic_monitor = None
    control_rx = None
    if config.REMOTE_MIC_ENABLED and config.RELAY_URL and config.RELAY_TOKEN:
        from remote_mic_receiver import RemoteMicReceiver
        remote_mic_rx = RemoteMicReceiver(
            config.RELAY_URL, config.RELAY_TOKEN, mic.router,
            on_log=console.log, key=config.ROOM_KEY,
            connect_timeout=config.RELAY_TIMEOUT_S,
        )
        remote_mic_rx.start()

        def _on_mic_switch(src):
            # 콘솔에 소스 전환을 가시화 + 웹으로도 상태 송신
            console.log(f"🎙️ 입력 소스 → {'원격(폰)' if src == 'remote' else '시스템'}")
            remote_mic_rx.notify_source(src)

        mic.router.on_switch = _on_mic_switch
        remote_mic_monitor = asyncio.create_task(mic.router.run_idle_monitor())
    # 웹 제어 채널(브라우저 → jarvis): 회의 종료 등 비오디오 명령. REMOTE_MIC 와 독립.
    if config.RELAY_URL and config.RELAY_TOKEN:
        from control_receiver import ControlReceiver

        async def _on_remote_command(msg):
            kind = msg.get("kind")
            if kind == "meeting_stop":
                await controller.stop_meeting()
            elif kind == "meeting_start":
                from live_translate import MeetingMeta
                title = (msg.get("title") or "").strip() or "회의"
                vocab = [v.strip() for v in (msg.get("vocabulary") or [])
                         if isinstance(v, str) and v.strip()]
                if not vocab:
                    vocab = ["Jarvis", config.USER_NAME]
                password = (msg.get("password") or "").strip()
                await controller.start_meeting(meta=MeetingMeta(
                    my_name=config.USER_NAME, title=title, vocabulary=vocab, password=password))
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
                llm.set_backend(settings.get("llm_backend"))
                console.log(f"⚙️ 설정 변경: {settings.current()}")
                if web_pub is not None:
                    web_pub.emit("settings", json.dumps(settings.current()))

        control_rx = ControlReceiver(
            config.RELAY_URL, config.RELAY_TOKEN,
            on_command=_on_remote_command, on_log=console.log,
            key=config.ROOM_KEY, connect_timeout=config.RELAY_TIMEOUT_S,
        )
    text_queue: asyncio.Queue[str] = asyncio.Queue()   # 텍스트 입력 대기열
    exit_event = asyncio.Event()           # /bye 등 명시적 종료 요청

    # 슬래시 명령 핸들러가 사용할 자원 컨텍스트 (commands.py 참고).
    # 컨트롤러 메서드(trigger_wake 등)는 controller 생성 후 주입한다.
    cmd_ctx = {
        "log": console.log,
        "set_status": console.set_status,
        "player": player,
        "tts": tts,
        "llm": llm,
        "request_exit": exit_event.set,
        "mic_router": (mic.router if config.REMOTE_MIC_ENABLED else None),
        "web_pub": web_pub,
    }

    recognizer = None   # 일반 대화 스트리밍 STT (없으면 배치 STT 폴백)

    async def cancel(task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def speak_response(text: str):
        """입력 텍스트 → LLM → TTS. 텍스트/오디오를 웹으로도 발행.
        TTS 는 원격 마이크 활성 시 웹(폰)으로만, 아니면 로컬 스피커로."""
        console.log(f"🧑 {text}")
        if web_pub is not None:
            web_pub.emit("user", text)
        console.set_status("생각 중…")
        first = True
        try:
            async for sentence in llm.respond(text):
                if first:
                    console.set_status(None)
                prefix = "🤖 " if first else "   "
                console.log(f"{prefix}{sentence}")
                first = False
                if web_pub is not None:
                    web_pub.emit("assistant", sentence)
                wav, sr = await tts.synth(sentence)
                if web_pub is not None and web_pub.web_viewer_count > 0:
                    pcm16 = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                    web_pub.emit_audio(pcm16, sr)
                    dur = len(wav) / float(sr)
                    controller.mark_web_speaking(dur)
                else:
                    await player.enqueue(wav, sr)
        finally:
            console.set_status(None)
        if first:
            pass

    async def _translate_bg(audio):
        """번역 모드 전용 백그라운드 파이프라인: STT → 번역 → 표시.
        컨트롤러가 즉시 다음 듣기로 복귀할 수 있도록 fire-and-forget.
        결과 순서는 끝나는 순서대로(짧은 발화가 먼저 보일 수 있음)."""
        try:
            text = await stt.transcribe(audio)
        except Exception as e:
            console.log(f"[stt] {e}")
            return
        if not text:
            return
        console.log(f"🧑 {text}")
        ko = await coach.translate_to_korean(llm.client, llm.model, text, llm.extra)
        if ko:
            console.log(f"🌐 {ko}")

    def _snapshot_queue() -> list[str]:
        """text_queue 의 현재 항목들을 비파괴적으로 스냅샷. 화면 표시용."""
        return list(text_queue._queue)   # asyncio.Queue 의 내부 deque (읽기 전용)

    def _refresh_queue_display():
        """입력 박스 위의 큐 표시를 현재 큐 상태로 갱신."""
        console.set_queue_display(_snapshot_queue())

    def _drain_text_queue() -> int:
        """대기 중인 텍스트 입력을 모두 폐기. 반환: 비운 개수."""
        n = 0
        while not text_queue.empty():
            try:
                text_queue.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                break
        _refresh_queue_display()
        return n

    def on_escape():
        if controller.in_meeting_setup():
            asyncio.create_task(controller._handle_setup_input("/cancel"))
            return
        if not text_queue.empty():
            _drain_text_queue()
            return
        asyncio.create_task(controller.request_stop())

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

    async def text_collector():
        """콘솔 입력을 받자마자 큐에 적재. 큐는 항상 '가장 최근 1건' 만 유지 —
        새 입력이 들어오면 이전 대기 입력은 폐기. 사용자가 답변 진행 중 여러 줄을
        쳤다면 마지막에 친 것만 다음에 처리된다."""
        async for line in console.lines():
            # 기존 대기 항목 비우고 최신 것 하나만 넣기
            while not text_queue.empty():
                try:
                    text_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            await text_queue.put(line)
            _refresh_queue_display()

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

    from streaming_stt import StreamingRecognizer
    from conversation_stt import ConversationSTT

    def _make_local():
        return StreamingRecognizer(
            on_partial=lambda t: controller.on_partial(t),
            on_final=lambda t: controller.on_final(t),
            model=config.MEET_STT_MODEL, realtime_model=config.MEET_STT_REALTIME_MODEL,
            language=config.WHISPER_LANG, on_log=console.log,
        )

    def _make_gladia():
        from gladia_stt import GladiaSTT
        langs = [s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()]
        return GladiaSTT(
            config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
            on_partial=lambda t: controller.on_partial(t),
            on_final=lambda t: controller.on_final(t), on_log=console.log,
        )

    recognizer = ConversationSTT(
        make_local=_make_local, make_gladia=_make_gladia,
        settings_get=settings.get, on_log=console.log,
    )

    from conversation import ConversationController

    async def _translate_audio(audio):
        await _translate_bg(audio)

    def _after_meeting_start(sess):
        # 원본 _begin_meeting 의 로그/리스너 등록 재현
        console.log(f"🎤 회의를 시작합니다. 회의 번호: {sess.meta.key}")
        if web_pub is not None:
            sess.add_listener(web_pub.emit_async)
            view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
            console.log(f"🔑 회의 ID: {sess.meta.meeting_id}")
            console.log(f"🌐 자막: {view_base}/{sess.meta.key}/meeting/{sess.meta.meeting_id}")
            web_pub.emit("meeting_title", sess.meta.title)

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
        if not commands.is_command(line):
            return None
        cmd_ctx["handled_state"] = False
        await commands.dispatch(line, cmd_ctx)
        return bool(cmd_ctx.get("handled_state"))

    store = MeetingStore("meetings.db")

    def _save_meeting(record):
        """종료 시 즉시 저장 → 트랜스크립트 있으면 백그라운드 요약 후 갱신."""
        async def _run():
            try:
                await asyncio.to_thread(store.save, record)
            except Exception as e:
                console.log(f"회의 저장 실패: {e}")
                return
            lines = record.get("transcript") or []
            if not lines:
                return
            text = "\n".join(
                (e.get("source") or "") +
                (f" / {e.get('ko') or e.get('en')}" if (e.get("ko") or e.get("en")) else "")
                for e in lines
            )
            try:
                summary = await llm.summarize(text)
            except Exception as e:
                console.log(f"회의 요약 실패: {e}")
                return
            if summary:
                try:
                    await asyncio.to_thread(store.set_summary, record["id"], summary)
                    console.log(f"📝 회의 요약 저장됨 (ID {record['id']})")
                except Exception as e:
                    console.log(f"요약 저장 실패: {e}")
        asyncio.create_task(_run())

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
        hands_free_timeout_s=config.HANDS_FREE_TIMEOUT_S,
        persist_mode=runtime_state.save_mode,
        save_meeting=_save_meeting,
    )

    cmd_ctx["trigger_wake"] = controller.on_wake
    cmd_ctx["start_translate"] = controller.start_translate
    cmd_ctx["stop_translate"] = controller.stop_translate
    cmd_ctx["start_meeting"] = lambda: controller.start_meeting(interactive=True)
    cmd_ctx["stop_meeting"] = controller.stop_meeting
    cmd_ctx["in_meeting"] = controller.in_meeting

    if control_rx is not None:
        control_rx.start()
    await recognizer.start()
    console.log("🗣️ 스트리밍 STT 준비됨 (호출어 후 실시간 인식)")

    console.set_escape_handler(on_escape)   # Esc → 진행 응답 취소
    console.set_empty_submit_allowed(controller.in_meeting_setup)   # 회의 설정 단계 Enter=기본
    _restore = runtime_state.load_mode()
    if _restore == "meeting":
        console.log("🎤 이전 회의 모드를 복구합니다.")
        await controller.start_meeting()
    elif _restore == "translate":
        console.log("🌐 이전 번역 모드를 복구합니다.")
        await controller.start_translate(None)
    else:
        await controller._set_idle()
    audio_task = asyncio.create_task(audio_loop())
    collector_task = asyncio.create_task(text_collector())
    worker_task = asyncio.create_task(text_worker())
    exit_task = asyncio.create_task(exit_event.wait())
    try:
        # 어느 하나라도 끝나면(예: 콘솔 EOF, /bye 등) 같이 정리
        done, _pending = await asyncio.wait(
            {audio_task, collector_task, worker_task, exit_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            if t is exit_task:
                continue
            exc = t.exception()
            if exc:
                raise exc
    finally:
        # 종료가 어디서 멈춰도 보장: 데몬 워치독이 일정 시간 뒤 강제 종료
        # (RealtimeSTT/sounddevice 의 비데몬 스레드·서브프로세스 잔존 또는 close 블로킹 대비).
        import threading
        _wd = threading.Timer(5.0, lambda: os._exit(0))
        _wd.daemon = True
        _wd.start()
        # console 을 먼저 멈춰야 collector 가 await 중인 prompt_async 가 풀린다.
        await console.stop()
        await cancel(audio_task)
        await cancel(collector_task)
        await cancel(worker_task)
        exit_task.cancel()
        await controller._cancel(controller.response)
        await controller._cancel(controller.watchdog)
        player_task.cancel()
        if web_pub is not None:
            try:
                await web_pub.close()
            except Exception:
                pass
        if remote_mic_monitor is not None:
            remote_mic_monitor.cancel()
        if remote_mic_rx is not None:
            try:
                await remote_mic_rx.close()
            except Exception:
                pass
        if control_rx is not None:
            try:
                await control_rx.close()
            except Exception:
                pass
        if recognizer is not None:
            try:
                await recognizer.aclose()
            except Exception:
                pass
        try:
            await backend.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
    finally:
        # 정리가 끝났어도 잔존 스레드/프로세스가 인터프리터 종료를 막는 경우가 있어 강제 종료.
        os._exit(0)
