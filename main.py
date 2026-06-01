"""Jarvis — 호출어 기반 로컬 음성 비서 오케스트레이터.

상태머신:
  WAITING_WAKE  'Hey Jarvis' 대기 (다른 소리·에코는 모두 무시)
       │ 호출 감지 → wake.wav
       ▼
  LISTENING     사용자 발화 캡처(VAD). 무발화 LISTEN_TIMEOUT_S 초 → 대기 복귀
       │ 발화 끝 → ok.wav
       ▼
  RESPONDING    STT → LLM(스트리밍) → TTS 재생
       │ 재생 중 'Hey Jarvis' → 재생 중단 + wake.wav → LISTENING
       │ 재생 완료 → WAITING_WAKE

이 구조 자체가 에코 루프를 막는다: RESPONDING 중 마이크로 들어온 에코는
utterance 가 돼도 무시되고, 오직 진짜 호출어만 상태를 전환한다.
"""
import asyncio

import config
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

    player_task = asyncio.create_task(player.run())
    state = "WAITING_WAKE"
    response: asyncio.Task | None = None   # 진행 중 응답 흐름
    watchdog: asyncio.Task | None = None   # LISTENING 타임아웃

    def idle():
        nonlocal state
        state = "WAITING_WAKE"
        print("\n🎙️  'Hey Jarvis' 라고 부르세요.")

    async def cancel(task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def respond_flow(audio):
        """입력 완료 → ok.wav → STT → LLM → TTS 재생 → 대기 복귀."""
        await player.enqueue_file(config.FX_OK)
        text = await stt.transcribe(audio)
        if text:
            print(f"🧑 {text}")
            print("🤖 ", end="", flush=True)
            async for sentence in llm.respond(text):
                print(sentence, end=" ", flush=True)
                wav, sr = await tts.synth(sentence)
                await player.enqueue(wav, sr)
            print()
        else:
            print("🧑 (인식된 음성 없음)")
        while player.is_speaking():     # 재생이 끝날 때까지 대기
            await asyncio.sleep(0.1)
        idle()

    async def listen_timeout():
        try:
            await asyncio.sleep(config.LISTEN_TIMEOUT_S)
        except asyncio.CancelledError:
            return
        if state == "LISTENING":
            print("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
            idle()

    idle()
    try:
        async for kind, audio in mic.events(
            wake_detect=wake.detect, is_speaking=player.is_speaking
        ):
            if kind == "wake":
                # 어느 상태에서든: 진행 중 응답 중단 → wake.wav → 듣기 시작
                await cancel(response); response = None
                await cancel(watchdog)
                player.flush()
                await player.enqueue_file(config.FX_WAKE)
                state = "LISTENING"
                print("🔔 듣고 있어요…")
                watchdog = asyncio.create_task(listen_timeout())

            elif kind == "utterance":
                # LISTENING 상태의 발화만 처리. (그 외 상태의 발화·에코는 무시)
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None
                    state = "RESPONDING"
                    response = asyncio.create_task(respond_flow(audio))
            # "start" 이벤트는 이 디자인에서 사용하지 않음
    finally:
        await cancel(response)
        await cancel(watchdog)
        player_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
