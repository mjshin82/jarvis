"""voice(TTS·효과음) 재생 어댑터 — 실제 출력은 backend 가 담당."""
import asyncio
import os

import numpy as np
import soundfile as sf


class Player:
    def __init__(self, backend):
        self.backend = backend
        self._fx_cache: dict[str, tuple] = {}

    def is_speaking(self) -> bool:
        return self.backend.is_speaking()

    async def run(self):
        while True:
            await asyncio.sleep(3600)

    async def enqueue(self, audio: np.ndarray, sr: int):
        await self.backend.play_voice(audio, sr)

    async def enqueue_file(self, path: str):
        if path not in self._fx_cache:
            if not os.path.exists(path):
                self._fx_cache[path] = None
            else:
                audio, sr = sf.read(path, dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                self._fx_cache[path] = (np.ascontiguousarray(audio), sr)
        item = self._fx_cache[path]
        if item is not None:
            await self.backend.play_voice(*item)

    def flush(self):
        self.backend.flush_voice()
