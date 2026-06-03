"""Jarvis — 호출어 기반 로컬 음성 비서 오케스트레이터.

상태머신(음성 흐름):
  WAITING_WAKE  'Hey Jarvis' 대기 (다른 소리·에코는 모두 무시)
       │ 호출 감지 → wake.wav
       ▼
  LISTENING     사용자 발화 캡처(VAD). 무발화 LISTEN_TIMEOUT_S 초 → 대기 복귀
       │ 발화 끝 → ok.wav
       ▼
  RESPONDING    STT → LLM(스트리밍) → TTS 재생
       │ 재생 중 'Hey Jarvis' / 텍스트 입력 → 재생 중단 → 새 요청 처리
       │ 재생 완료 → WAITING_WAKE

이 구조 자체가 에코 루프를 막는다: RESPONDING 중 마이크로 들어온 에코는
utterance 가 돼도 무시되고, 오직 진짜 호출어/텍스트 입력만 상태를 전환한다.

콘솔 입력: 화면 하단의 '> ' 프롬프트에 텍스트를 치면 호출어 없이도 곧장
LLM 으로 흘러간다(STT 만 건너뛰고 이후 파이프라인은 동일). 진행 중 응답은
자동 중단(barge-in).
"""
import asyncio

import config
import console
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
    response: asyncio.Task | None = None   # 진행 중 응답 흐름
    watchdog: asyncio.Task | None = None   # LISTENING 타임아웃

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
        """입력 텍스트(STT 결과 또는 콘솔 입력) → LLM → TTS 재생."""
        console.log(f"🧑 {text}")
        console.log("🤖 ", end="", flush=True)
        async for sentence in llm.respond(text):
            console.log(sentence, end=" ", flush=True)
            wav, sr = await tts.synth(sentence)
            await player.enqueue(wav, sr)
        console.log("")

    async def respond_flow_audio(audio):
        """음성 입력 흐름: ok.wav → STT → 공통 응답 → (연속대화면) 다시 듣기."""
        await player.enqueue_file(config.FX_OK)
        text = await stt.transcribe(audio)
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
        """텍스트 입력 흐름: STT 건너뛰고 곧장 응답. 후속은 다시 대기로."""
        await speak_response(text)
        while player.is_speaking():
            await asyncio.sleep(0.1)
        idle()

    async def listen_timeout():
        try:
            await asyncio.sleep(config.LISTEN_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if state == "LISTENING":
            console.log("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
            idle()

    async def audio_loop():
        """마이크 이벤트 소비. main() 본체에서 분리해 텍스트 입력과 동시 실행."""
        nonlocal state, response, watchdog
        async for kind, audio in mic.events(
            wake_detect=wake.detect, is_speaking=player.is_speaking
        ):
            if kind == "wake":
                # 어느 상태에서든: 진행 중 응답 중단 → wake.wav → 듣기 시작
                await cancel(response); response = None
                player.flush()
                await enter_listening(cue=True)
            elif kind == "start":
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None
            elif kind == "utterance":
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None
                    state = "RESPONDING"
                    response = asyncio.create_task(respond_flow_audio(audio))

    async def text_loop():
        """콘솔 입력 소비. 어느 상태에서도 입력은 곧장 새 요청으로(자동 barge-in)."""
        nonlocal state, response, watchdog
        async for line in console.lines():
            await cancel(response); response = None
            await cancel(watchdog); watchdog = None
            player.flush()
            state = "RESPONDING"
            response = asyncio.create_task(respond_flow_text(line))

    idle()
    audio_task = asyncio.create_task(audio_loop())
    text_task = asyncio.create_task(text_loop())
    try:
        # 둘 중 하나라도 끝나면(예: 콘솔 EOF) 같이 정리
        done, _pending = await asyncio.wait(
            {audio_task, text_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            exc = t.exception()
            if exc:
                raise exc
    finally:
        await cancel(audio_task)
        await cancel(text_task)
        await cancel(response)
        await cancel(watchdog)
        player_task.cancel()
        console.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
