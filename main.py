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
from audio_io import Microphone
from stt import STT
from llm import LLM
from tts import TTS
from player import Player
from wake import WakeWord


async def main():
    mic = Microphone()
    stt = STT()
    llm = LLM()
    tts = TTS()
    player = Player()
    wake = WakeWord()

    await llm.warmup()
    console.start()                                 # 콘솔 입력 활성화 (하단 프롬프트)
    player_task = asyncio.create_task(player.run())
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
        """듣기 상태로 진입: (선택) wake.wav 큐 → LISTENING → 무발화 타임아웃 감시."""
        nonlocal state, watchdog
        await cancel(watchdog)
        if cue:
            await player.enqueue_file(config.FX_WAKE)
        state = "LISTENING"
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

    async def respond_flow_audio(audio):
        """음성 입력 흐름: ok.wav → STT → 공통 응답 → (연속대화면) 다시 듣기."""
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
          1) 큐에 대기 입력 있으면 → 큐만 비움 (진행 중 응답은 계속)
          2) 큐 비고 응답 진행 중이면 → 응답 취소
          3) 아무 것도 없으면 → 무동작
        console 이벤트 루프 안에서 호출되므로 await 가 필요한 작업은 task 로."""
        # 우선순위 1: 큐가 있으면 큐만 비움 (화면의 ⏳ 표시가 사라지는 것으로 충분)
        if not text_queue.empty():
            _drain_text_queue()
            return
        # 우선순위 2: 응답 진행 중이면 응답 취소
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

    cmd_ctx["trigger_wake"] = trigger_wake

    async def audio_loop():
        """마이크 이벤트 소비."""
        nonlocal state, response, watchdog
        async for kind, audio in mic.events(
            wake_detect=wake.detect, is_speaking=player.is_speaking
        ):
            if kind == "wake":
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
