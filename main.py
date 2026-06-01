"""Jarvis — 로컬 음성 비서 파이프라인 오케스트레이터 (barge-in 지원).

    마이크 → VAD → Moonshine STT → DeepSeek V4 (스트리밍) → Supertonic TTS → 스피커

핵심 1: LLM 이 문장을 하나 뱉을 때마다 즉시 TTS→재생 큐로 흘려보내,
        LLM 이 답을 다 끝내기 전에 첫 문장부터 말하기 시작한다.
핵심 2: 응답을 별도 태스크로 돌려, 말하는 중에도 마이크를 계속 듣는다.
        사용자가 말을 시작하면(VAD 'start') 진행 중 응답을 즉시 끊는다(barge-in).
"""
import asyncio

from audio_io import Microphone
from stt import STT
from llm import LLM
from tts import TTS
from player import Player


async def handle_utterance(audio, stt, llm, tts, player):
    """발화 하나를 받아 텍스트화 → 응답 생성 → 문장별 합성/재생.

    barge-in 시 이 태스크가 통째로 cancel 된다 (LLM 스트림·TTS·enqueue 중단).
    """
    user_text = await stt.transcribe(audio)
    if not user_text:
        return
    print(f"\n🧑 {user_text}")

    print("🤖 ", end="", flush=True)
    async for sentence in llm.respond(user_text):
        print(sentence, end=" ", flush=True)
        wav, sr = await tts.synth(sentence)   # 문장 합성
        await player.enqueue(wav, sr)         # 순서대로 재생 큐에
    print()


async def main():
    mic = Microphone()
    stt = STT()
    llm = LLM()
    tts = TTS()
    player = Player()

    player_task = asyncio.create_task(player.run())  # 재생 소비자 상시 가동
    response: asyncio.Task | None = None             # 진행 중인 응답 태스크

    async def interrupt():
        """진행 중 응답을 끊는다: 태스크 취소 + 재생 중단 + 큐 비우기."""
        nonlocal response
        if response and not response.done():
            response.cancel()
            try:
                await response          # finally(대화기록 정리)까지 마무리 대기
            except asyncio.CancelledError:
                pass
            print("  ⏹️  (중단됨)")
        player.flush()

    print("🎙️  Jarvis 준비 완료. 말씀하세요. (Ctrl+C 로 종료)")
    try:
        async for kind, audio in mic.events(is_speaking=player.is_speaking):
            if kind == "start":
                # 사용자가 말을 시작 → 자비스가 말하는 중이면 즉시 멈춤
                await interrupt()
            elif kind == "utterance":
                # 발화 완료 → 응답을 별도 태스크로 시작 (그동안 마이크 계속 청취)
                response = asyncio.create_task(
                    handle_utterance(audio, stt, llm, tts, player)
                )
    finally:
        await interrupt()
        player_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
