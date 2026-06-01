"""STT 래퍼 — faster-whisper (로컬, CTranslate2).

다국어 고품질 모델(large-v3-turbo). 언어를 한국어로 고정해 환각을 줄인다.
추론은 동기/CPU 바운드라 asyncio.to_thread 로 감싼다.
모델은 첫 실행 시 HuggingFace 에서 자동 다운로드되어 캐시된다.
"""
import asyncio

import numpy as np
from faster_whisper import WhisperModel

import config

_MIN_SAMPLES = 1600    # 0.1s @ 16kHz — 그보다 짧으면 무의미
_MIN_RMS = 0.01        # 이보다 조용하면 무음으로 보고 STT 생략


class STT:
    def __init__(self):
        self.model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
        self.lang = config.WHISPER_LANG
        print(
            f"[stt] faster-whisper {config.WHISPER_MODEL} "
            f"({config.WHISPER_DEVICE}/{config.WHISPER_COMPUTE}), lang={self.lang}"
        )

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        audio = np.ascontiguousarray(audio.squeeze(), dtype=np.float32)
        if audio.size < _MIN_SAMPLES:
            return ""
        if float(np.sqrt(np.mean(audio ** 2))) < _MIN_RMS:
            return ""
        segments, _info = self.model.transcribe(
            audio,
            language=self.lang,
            beam_size=1,         # 그리디 → 저지연
            vad_filter=True,     # 비음성 구간 제거(환각 방지)
        )
        return "".join(seg.text for seg in segments).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio)
