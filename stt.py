"""STT 래퍼 — faster-whisper (로컬, CTranslate2).

다국어 고품질 모델(large-v3-turbo). 언어를 한국어로 고정해 환각을 줄인다.
추론은 동기/CPU 바운드라 asyncio.to_thread 로 감싼다.
모델은 첫 실행 시 HuggingFace 에서 자동 다운로드되어 캐시된다.
"""
import asyncio

import numpy as np
from faster_whisper import WhisperModel

import config
import wordbook
from simulation import MODE

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
        self.initial_prompt = wordbook.load_initial_prompt()  # 어휘 컨디셔닝
        print(
            f"[stt] faster-whisper {config.WHISPER_MODEL} "
            f"({config.WHISPER_DEVICE}/{config.WHISPER_COMPUTE}), lang={self.lang}"
        )
        if self.initial_prompt:
            print(f"[stt] 워드북 적용: {self.initial_prompt[:80]}…")

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        audio = np.ascontiguousarray(audio.squeeze(), dtype=np.float32)
        if audio.size < _MIN_SAMPLES:
            return ""
        if float(np.sqrt(np.mean(audio ** 2))) < _MIN_RMS:
            return ""
        # 번역 모드는 의도적으로 자동 감지(language=None) 를 쓸 수 있다.
        # 그래서 'or self.lang' 폴백을 하면 안 됨 — 자동 감지가 ko 로 덮여버린다.
        if MODE.is_translate():
            lang = MODE.stt_lang()   # None 그대로 (자동 감지)
        else:
            lang = MODE.stt_lang() or self.lang
        # 상태 특화 프롬프트가 있으면 그걸 우선. 한국어 워드북은 한국어 모드에서만.
        prompt = MODE.stt_initial_prompt()
        if prompt is None and lang == "ko":
            prompt = self.initial_prompt
        segments, _info = self.model.transcribe(
            audio,
            language=lang,
            beam_size=1,         # 그리디 → 저지연
            vad_filter=True,     # 비음성 구간 제거(환각 방지)
            initial_prompt=prompt,
        )
        text = "".join(seg.text for seg in segments).strip()
        if lang == "ko":
            text = wordbook.apply_aliases(text)
        return text

    async def transcribe(self, audio: np.ndarray) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio)
