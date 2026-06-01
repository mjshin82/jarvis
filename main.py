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
from audio_backend import make_backend
from audio_io import Microphone
from stt import STT
from llm import LLM
from tts import TTS
from player import Player
from wake import WakeWord


async def main():
    backend = make_backend()
    try:
        await backend.start()
    except Exception as e:
        print(f"[audio] AEC 백엔드 기동 실패 → sounddevice 폴백: {e}")
        from audio_backend import SounddeviceBackend
        backend = SounddeviceBackend()
        await backend.start()
    mic = Microphone(backend)
    stt = STT()
    llm = LLM(backend)
    tts = TTS()
    player = Player(backend)
    wake = WakeWord()

    await llm.warmup()                              # 모델 예열 → 첫 응답 지연 제거
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

    async def enter_listening(cue=True):
        """듣기 상태로 진입: (선택) wake.wav 큐 → LISTENING → 무발화 타임아웃 감시."""
        nonlocal state, watchdog
        await cancel(watchdog)
        if cue:
            await player.enqueue_file(config.FX_WAKE)
        state = "LISTENING"
        print("🔔 듣고 있어요…")
        watchdog = asyncio.create_task(listen_timeout())

    async def respond_flow(audio):
        """입력 완료 → ok.wav → STT → LLM → TTS 재생 → (연속대화면) 다시 듣기."""
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
        # 음악을 틀었으면 → 호출어 대기로(음악 소리가 마이크로 들어와 오인되는 것 방지).
        # 그 외에는 연속 대화(FOLLOW_UP) 시 바로 다시 듣기.
        if "play_music" in llm.last_tool_names:
            idle()
        elif config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
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
                player.flush()
                await enter_listening(cue=True)

            elif kind == "start":
                # 사용자가 말을 시작 → 타임아웃 취소 (긴 발화 중 대기 복귀 방지)
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None

            elif kind == "utterance":
                # LISTENING 상태의 발화만 처리. (그 외 상태의 발화·에코는 무시)
                if state == "LISTENING":
                    await cancel(watchdog); watchdog = None
                    state = "RESPONDING"
                    response = asyncio.create_task(respond_flow(audio))
    finally:
        await cancel(response)
        await cancel(watchdog)
        player_task.cancel()
        await backend.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
