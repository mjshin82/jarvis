"""Moonshine STT 래퍼 (로컬, ONNX).

Moonshine 추론은 동기 + CPU 바운드라서 asyncio.to_thread 로 감싼다.
"""
import asyncio

import numpy as np
import moonshine_onnx  # useful-moonshine-onnx 패키지가 제공하는 모듈명

import config


class STT:
    def __init__(self):
        self.model = config.MOONSHINE_MODEL

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        # moonshine 의 transcribe 가 내부에서 배치 차원을 붙이므로 1D float32 16kHz 로 넘긴다.
        audio = np.ascontiguousarray(audio.squeeze(), dtype=np.float32)
        text = moonshine_onnx.transcribe(audio, self.model)
        # transcribe 는 list[str] 형태로 줄 수 있으니 정규화
        if isinstance(text, (list, tuple)):
            text = " ".join(text)
        return text.strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio)
