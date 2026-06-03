"""Supertonic TTS 래퍼 (로컬, ONNX).

supertonic 패키지의 고수준 API 사용. 모델은 첫 실행 시 HuggingFace
(Supertone/supertonic-3)에서 자동 다운로드되어 캐시된다.
합성은 동기/CPU 바운드라 asyncio.to_thread 로 감싼다.

synthesize() 반환: (wav, duration)
  wav      : np.ndarray, shape (1, N), float32  (-1.0~1.0)
  duration : 합성 길이(초)
출력 샘플레이트는 tts.sample_rate (supertonic-3 = 44100Hz).
"""
import asyncio

import numpy as np
from supertonic import TTS as SupertonicTTS

import config
from simulation import MODE
from text_norm import normalize


class TTS:
    def __init__(self):
        # auto_download=True: 최초 1회 모델 받고 이후 캐시 사용
        self.engine = SupertonicTTS(
            model=config.SUPERTONIC_MODEL,
            auto_download=True,
        )
        # 보이스 스타일은 이름 단위로 캐시(시뮬 모드는 다른 음성을 쓸 수 있음)
        self._styles: dict[str, object] = {}
        self.sample_rate = int(getattr(self.engine, "sample_rate", 44_100))

    def _get_style(self, voice: str):
        if voice not in self._styles:
            self._styles[voice] = self.engine.get_voice_style(voice)
        return self._styles[voice]

    def _synth_sync(self, text: str) -> np.ndarray:
        lang = MODE.tts_lang()
        # 합성 직전 텍스트 정규화: 숫자/단위/기호를 발화 가능한 형태로 변환
        # (Supertonic 의 G2P 가 약한 부분을 코드에서 보완 → 발음 안정성↑)
        text = normalize(text, lang=lang)
        wav, _dur = self.engine.synthesize(
            text=text,
            voice_style=self._get_style(MODE.tts_voice()),
            lang=lang,
            speed=config.SUPERTONIC_SPEED,
            total_steps=config.SUPERTONIC_STEPS,
        )
        # (1, N) → (N,) float32 로 정규화 (player/sounddevice 용)
        return np.ascontiguousarray(np.asarray(wav).squeeze(), dtype=np.float32)

    async def synth(self, text: str) -> tuple[np.ndarray, int]:
        audio = await asyncio.to_thread(self._synth_sync, text)
        return audio, self.sample_rate
