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

import config
import console
import commands
import coach
from audio_io import Microphone
from audio_backend import make_backend
from stt import STT
from llm import LLM
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
    # 원격 마이크 (옵션): 웹 프론트가 보내는 외부 마이크 스트림을 relay 역방향으로 수신.
    remote_mic_rx = None
    remote_mic_monitor = None
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
        cap_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
        cap_url = f"{cap_base}/m/{config.ROOM_KEY}"
        box_width = max(len(cap_url) + 4, 60)
        border = "─" * box_width
        console.log("")
        console.log(f"┌{border}┐")
        console.log(f"│  📱 회의/원격마이크 페이지 (admin 로그인 후 mic 토글)".ljust(box_width + 1) + "│")
        console.log(f"│  {cap_url}".ljust(box_width + 1) + "│")
        console.log(f"└{border}┘")
        console.log("")
    state = "WAITING_WAKE"
    response: asyncio.Task | None = None   # 진행 중 응답 흐름 (텍스트 또는 음성)
    watchdog: asyncio.Task | None = None   # LISTENING 타임아웃
    text_queue: asyncio.Queue[str] = asyncio.Queue()   # 텍스트 입력 대기열
    exit_event = asyncio.Event()           # /bye 등 명시적 종료 요청

    # 슬래시 명령 핸들러가 사용할 자원 컨텍스트 (commands.py 참고).
    # trigger_wake 는 함수 정의 후 main() 본체에서 한 번 더 주입한다.
    cmd_ctx = {
        "log": console.log,
        "set_status": console.set_status,
        "player": player,
        "tts": tts,
        "llm": llm,
        "request_exit": exit_event.set,
        "mic_router": (mic.router if config.REMOTE_MIC_ENABLED else None),
    }

    def idle():
        nonlocal state
        state = "WAITING_WAKE"
        console.log("\n🎙️  'Hey Jarvis' 라고 부르거나 아래에 텍스트를 입력하세요.")

    async def cancel(task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def enter_listening(cue=True):
        """듣기 상태로 진입: (선택) wake.wav 큐 → LISTENING → 무발화 타임아웃 감시.
        번역 모드는 끝없이 듣기를 반복하므로 안내 한 줄을 매번 찍지 않는다(노이즈)."""
        nonlocal state, watchdog
        await cancel(watchdog)
        if cue:
            await player.enqueue_file(config.FX_WAKE)
        state = "LISTENING"
        if not MODE.is_translate():
            console.log("🔔 듣고 있어요…")
        watchdog = asyncio.create_task(listen_timeout())

    async def speak_response(text: str):
        """입력 텍스트(STT 결과 또는 콘솔 입력) → LLM → TTS 재생.
        각 문장은 별도의 한 줄로 출력한다. prompt_toolkit 의 patch_stdout 은
        '완전한 한 줄' 단위로만 입력 박스 위에 누적하기 때문에, end='' 로
        부분 라인을 흘리면 입력 박스 영역과 섞여 사라진다."""
        console.log(f"🧑 {text}")
        console.set_status("생각 중…")   # 첫 문장 나올 때까지 진행 표시
        first = True
        try:
            async for sentence in llm.respond(text):
                if first:
                    console.set_status(None)   # 응답 시작 → 표시 끄기
                prefix = "🤖 " if first else "   "
                console.log(f"{prefix}{sentence}")
                first = False
                wav, sr = await tts.synth(sentence)
                await player.enqueue(wav, sr)
        finally:
            console.set_status(None)   # 예외/취소 시에도 반드시 끄기
        if first:
            # 응답이 비어있었던 경우(드뭄): 빈 🤖 줄로 표시는 생략
            pass

    async def _translate_bg(audio):
        """번역 모드 전용 백그라운드 파이프라인: STT → 번역 → 표시.
        respond_flow_audio 가 즉시 다음 듣기로 복귀할 수 있도록 fire-and-forget.
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

    async def respond_flow_audio(audio):
        """음성 입력 흐름.
        - 평상시: ok.wav → STT → LLM 응답 → TTS → 다시 듣기 (직렬)
        - 번역 모드: 효과음 X + STT/번역까지 통째로 백그라운드, 즉시 다시 듣기.
          → 연속 발화 중간에 마이크가 안 끊기고 효과음 되먹임도 없다."""
        if MODE.is_translate():
            # 효과음 X (말 위에 노이즈 안 끼게), STT+번역 통째로 백그라운드 위탁
            asyncio.create_task(_translate_bg(audio))
            await enter_listening(cue=False)
            return

        await player.enqueue_file(config.FX_OK)
        console.set_status("받아쓰는 중…")
        try:
            text = await stt.transcribe(audio)
        finally:
            console.set_status(None)
        if text:
            await speak_response(text)
        else:
            console.log("🧑 (인식된 음성 없음)")
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
            idle()

    async def respond_flow_text(text: str):
        """텍스트 입력 흐름. 슬래시(/) 로 시작하면 명령으로 디스패치, 아니면 LLM 응답.
        둘 다 같은 큐잉/Esc 정책을 따른다 — text_worker 가 한 번에 하나씩만 실행.

        명령이 자체적으로 상태를 잡은 경우(예: /mic → LISTENING) 후처리 idle 을
        건너뛴다. cmd_ctx['handled_state'] 가 True 이면 명령이 상태를 책임짐."""
        cmd_ctx["handled_state"] = False
        if commands.is_command(text):
            await commands.dispatch(text, cmd_ctx)
        else:
            await speak_response(text)
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if not cmd_ctx.get("handled_state"):
            idle()

    async def listen_timeout():
        try:
            await asyncio.sleep(config.LISTEN_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        # 번역 모드는 사용자가 /stop 으로만 빠져나가므로 타임아웃 무효
        if MODE.is_translate():
            return
        if state == "LISTENING":
            console.log("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
            idle()

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
        """Esc 콜백 — 단계적 취소.
          1) 회의 메타 입력 중이면 → 메타 입력 취소
          2) 큐에 대기 입력 있으면 → 큐만 비움 (진행 중 응답은 계속)
          3) 큐 비고 응답 진행 중이면 → 응답 취소
          4) 아무 것도 없으면 → 무동작
        console 이벤트 루프 안에서 호출되므로 await 가 필요한 작업은 task 로."""
        # 우선순위 1: 회의 메타 입력 단계 취소
        if in_meeting_setup():
            cancel_meeting_setup()
            return
        # 우선순위 2: 큐가 있으면 큐만 비움
        if not text_queue.empty():
            _drain_text_queue()
            return
        # 우선순위 3: 응답 진행 중이면 응답 취소
        if response is not None and not response.done():
            asyncio.create_task(_cancel_response_and_idle())

    async def _cancel_response_and_idle():
        nonlocal response
        await cancel(response); response = None
        player.flush()
        console.set_status(None)
        console.log("⏹  진행 중 응답을 멈췄어요.")
        idle()

    async def trigger_wake():
        """음성 'Hey Jarvis' 와 동일한 효과: 응답 중단 + 큐 비움 + 듣기 시작.
        slash 명령(/mic) 에서도 그대로 재사용."""
        nonlocal response
        await cancel(response); response = None
        player.flush()
        _drain_text_queue()
        await enter_listening(cue=True)

    async def start_translate(src_lang: str | None):
        """번역 모드 진입: 모든 발화를 받아 한국어로 옮긴다. /stop 까지 무한 듣기."""
        nonlocal response
        await cancel(response); response = None
        player.flush()
        _drain_text_queue()
        MODE.start_translate(src_lang=src_lang)
        suffix = f" (입력 언어: {src_lang})" if src_lang else " (입력 언어 자동 감지)"
        console.log(f"🌐 번역 모드 시작{suffix}. 끝내려면 /stop.")
        await enter_listening(cue=False)

    async def stop_translate():
        """번역 모드 종료 → 평상시 대기로."""
        if not MODE.is_translate():
            console.log("번역 모드가 아닙니다.")
            return
        MODE.end_translate()
        console.log("🌐 번역 모드 종료.")
        idle()

    # --- 회의 모드 (/meet) ---
    # 두 단계: meeting_setup(메타 입력 대기) → meeting_session(실제 회의)
    meeting_session: dict = {"obj": None}
    meeting_setup: dict = {"obj": None}

    def in_meeting_setup() -> bool:
        return meeting_setup["obj"] is not None

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
        meeting_setup["obj"] = setup
        console.log(f"🎤 회의 시작 전 정보를 입력해주세요. (내 이름: {config.USER_NAME}, Esc 로 취소)")
        console.log(f"   {setup.prompt}")

    def cancel_meeting_setup() -> bool:
        """메타 입력 도중 취소(Esc). True 면 실제로 취소함."""
        if meeting_setup["obj"] is None:
            return False
        meeting_setup["obj"] = None
        console.log("🎤 회의 시작을 취소했어요.")
        idle()
        return True

    async def handle_meeting_setup_input(line: str) -> None:
        """메타 입력 단계 한 줄 처리. 다 채우면 실제 회의 시작."""
        setup: "live_translate.MeetingSetup" = meeting_setup["obj"]
        if setup is None:
            return
        # /bye 같은 명령은 그대로 통과시키지 말고 가벼운 안내. 단 /stop 으로는 취소 가능
        stripped = line.strip()
        if stripped.lower() in ("/stop", "/cancel", "취소"):
            cancel_meeting_setup()
            return
        if not stripped:
            console.log(f"   {setup.prompt}")
            return
        setup.submit(stripped)
        if not setup.done:
            console.log(f"   {setup.prompt}")
            return
        # 완료 → 실제 회의 시작.
        # 현재 _META_STEPS 가 비어 이 경로는 도달하지 않음(시작은 start_meeting_setup 에서).
        # 향후 메타 입력 단계가 생기면 use_remote 를 여기까지 이어줘야 한다.
        meta = setup.meta
        meeting_setup["obj"] = None
        await _begin_meeting(meta)

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
            # 외부 중계 활성 (옵션) — 자막 페이지 URL 을 박스로 강조 표시
            if config.RELAY_URL and config.RELAY_TOKEN:
                from relay_client import RelayClient
                relay = RelayClient(
                    config.RELAY_URL, config.RELAY_TOKEN, meta,
                    on_log=console.log,
                    connect_timeout=config.RELAY_TIMEOUT_S,
                )
                ok = await relay.connect()
                if ok:
                    sess.add_listener(relay.emit_async)
                    sess._relay = relay   # stop() 에서 close
                    # http 보기 URL 안내 (ws → http 로 단순 치환)
                    view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
                    view_url = f"{view_base}/m/{meta.key}"
                    box_width = max(len(view_url) + 4, 60)
                    border = "─" * box_width
                    console.log("")
                    console.log(f"┌{border}┐")
                    console.log(f"│  🌐 자막 페이지 (이 URL 을 참석자에게 공유)".ljust(box_width + 1) + "│")
                    console.log(f"│  {view_url}".ljust(box_width + 1) + "│")
                    console.log(f"└{border}┘")
                    console.log("")
                else:
                    console.log("🌐 중계 서버 연결 실패 — 콘솔만으로 진행합니다.")
        except Exception as ex:
            mic.resume()
            console.log(f"회의 모드 시작 실패: {ex}")

    async def stop_meeting():
        # 메타 입력 중이었으면 그것부터 취소
        if cancel_meeting_setup():
            return
        sess = meeting_session["obj"]
        if sess is None:
            console.log("회의 모드가 아닙니다.")
            return
        try:
            await sess.stop()
        finally:
            mic.router.set_tap(None)   # 원격 프레임을 메인 경로로 복귀
            meeting_session["obj"] = None
            mic.resume()
            console.set_status(None)
            idle()

    cmd_ctx["trigger_wake"] = trigger_wake
    cmd_ctx["start_translate"] = start_translate
    cmd_ctx["stop_translate"] = stop_translate
    cmd_ctx["start_meeting"] = start_meeting_setup    # 명령은 셋업부터
    cmd_ctx["stop_meeting"] = stop_meeting
    cmd_ctx["in_meeting"] = lambda: meeting_session["obj"] is not None or meeting_setup["obj"] is not None

    async def audio_loop():
        """마이크 이벤트 소비."""
        nonlocal state, response, watchdog
        async for kind, audio in mic.events(
            wake_detect=wake.detect, is_speaking=player.is_speaking
        ):
            if kind == "wake":
                # 번역 모드 중에는 호출어로 빠져나가지 않는다 (/stop 만 종료)
                if MODE.is_translate():
                    continue
                await trigger_wake()
            elif kind == "start":
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None
            elif kind == "utterance":
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None
                    state = "RESPONDING"
                    response = asyncio.create_task(respond_flow_audio(audio))

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
        """텍스트 큐를 직렬로 소비. 진행 중 응답이 끝난 다음에 큐에서 빼서 처리한다 —
        '꺼낸 직후 대기'를 하면 그 사이 화면에서 항목이 사라져 사용자가 헷갈린다."""
        nonlocal state, response, watchdog
        while True:
            # 진행 중 응답이 있으면 먼저 끝날 때까지 대기 (큐 빼지 말 것)
            if response is not None and not response.done():
                try:
                    await response
                except (asyncio.CancelledError, Exception):
                    pass
            # 이제 큐에서 가장 최근 입력을 가져옴 (collector 가 1개만 유지하므로
            # 사실상 peek-and-pop). 큐가 비어있으면 다음 입력 들어올 때까지 대기.
            line = await text_queue.get()
            _refresh_queue_display()   # 빼냄 즉시 화면 갱신
            # 회의 메타 입력 단계면 메타 라우터로 직행 (LLM 응답 흐름 X)
            if in_meeting_setup():
                await handle_meeting_setup_input(line)
                continue
            if watchdog is not None:
                await cancel(watchdog); watchdog = None
            state = "RESPONDING"
            response = asyncio.create_task(respond_flow_text(line))

    console.set_escape_handler(on_escape)   # Esc → 진행 응답 취소
    idle()
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
        # console 을 먼저 멈춰야 collector 가 await 중인 prompt_async 가 풀린다.
        await console.stop()
        await cancel(audio_task)
        await cancel(collector_task)
        await cancel(worker_task)
        exit_task.cancel()
        await cancel(response)
        await cancel(watchdog)
        player_task.cancel()
        if remote_mic_monitor is not None:
            remote_mic_monitor.cancel()
        if remote_mic_rx is not None:
            try:
                await remote_mic_rx.close()
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
