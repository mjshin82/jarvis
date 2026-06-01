"""순서 보장 오디오 재생기 (barge-in 지원).

문장별로 합성된 오디오를 큐에 받아 '들어온 순서대로' 재생한다.
재생(블로킹)은 to_thread 로 돌려 이벤트 루프를 막지 않는다.
합성(producer)과 재생(consumer)이 분리되어 N+1 문장을 합성하는 동안
N 문장을 재생 → 파이프라인이 끊기지 않는다.

barge-in: flush() 가 현재 재생을 즉시 끊고(sd.stop) 대기 중인 큐를 비운다.
"""
import asyncio
import os

import numpy as np
import sounddevice as sd
import soundfile as sf


class Player:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._playing = False   # 지금 한 청크를 실제로 재생 중인지
        self._fx_cache: dict[str, tuple] = {}   # 효과음 wav 캐시

    def is_speaking(self) -> bool:
        """자비스가 말하는 중인가? (재생 중이거나 재생 대기 큐가 남았으면 True)"""
        return self._playing or not self.queue.empty()

    def _play_sync(self, audio: np.ndarray, sr: int):
        sd.play(audio, sr)
        sd.wait()   # flush() 의 sd.stop() 이 호출되면 즉시 반환된다

    async def run(self):
        """앱 수명 동안 도는 소비자 태스크."""
        while True:
            audio, sr = await self.queue.get()
            if len(audio):
                self._playing = True
                try:
                    await asyncio.to_thread(self._play_sync, audio, sr)
                finally:
                    self._playing = False

    async def enqueue(self, audio: np.ndarray, sr: int):
        await self.queue.put((audio, sr))

    async def enqueue_file(self, path: str):
        """효과음 wav 를 재생 큐에 넣는다. 파일이 없으면 조용히 무시."""
        if path not in self._fx_cache:
            if not os.path.exists(path):
                self._fx_cache[path] = None
            else:
                audio, sr = sf.read(path, dtype="float32")
                if audio.ndim > 1:                 # 스테레오 → 모노
                    audio = audio.mean(axis=1)
                self._fx_cache[path] = (np.ascontiguousarray(audio), sr)
        item = self._fx_cache[path]
        if item is not None:
            await self.enqueue(*item)

    def flush(self):
        """barge-in: 대기 큐를 비우고 현재 재생을 중단한다."""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        sd.stop()   # 재생 중이던 _play_sync 의 sd.wait() 가 풀린다
