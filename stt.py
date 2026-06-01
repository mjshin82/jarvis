"""Moonshine 한국어 STT 래퍼 (로컬, ONNX).

useful-moonshine-onnx 패키지는 영어 repo/토크나이저에 하드코딩돼 있으므로,
한국어 ONNX 모델(onnx-community/moonshine-base-ko-ONNX)과 그 자체 토크나이저를
직접 받아 연결한다. 추론은 동기/CPU 바운드라 asyncio.to_thread 로 감싼다.
"""
import asyncio
import json
import os

import numpy as np
import tokenizers
from huggingface_hub import hf_hub_download
from moonshine_onnx import MoonshineOnnxModel

import config

_MIN_SAMPLES = 1600    # 0.1s @ 16kHz — 그보다 짧으면 무의미
_MIN_RMS = 0.01        # 이보다 조용하면 무음으로 보고 STT 생략 (모델 환각 방지)


class STT:
    def __init__(self):
        repo = config.MOONSHINE_REPO
        # 인코더/디코더 onnx 는 같은 snapshot 의 onnx/ 폴더로 받아진다
        enc = hf_hub_download(repo, "onnx/encoder_model.onnx")
        hf_hub_download(repo, "onnx/decoder_model_merged.onnx")
        tok = hf_hub_download(repo, "tokenizer.json")
        models_dir = os.path.dirname(enc)

        self.model = MoonshineOnnxModel(
            models_dir=models_dir, model_name=config.MOONSHINE_ARCH
        )
        # 한국어 토크나이저로 디코딩 (영어 토크나이저로는 한국어가 안 나옴)
        self.tokenizer = tokenizers.Tokenizer.from_file(tok)

        # 특수 토큰 id 는 모델마다 다를 수 있어 generation_config 로 보정
        try:
            gcfg = hf_hub_download(repo, "generation_config.json")
            with open(gcfg) as f:
                g = json.load(f)
            if "decoder_start_token_id" in g:
                self.model.decoder_start_token_id = g["decoder_start_token_id"]
            if "eos_token_id" in g:
                eos = g["eos_token_id"]
                self.model.eos_token_id = eos[0] if isinstance(eos, list) else eos
        except Exception:
            pass  # 없으면 패키지 기본값(1/2) 사용

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        audio = np.ascontiguousarray(audio.squeeze(), dtype=np.float32)
        if audio.size < _MIN_SAMPLES:
            return ""
        # 무음/저음량은 모델이 환각(반복 토큰)을 내므로 STT 자체를 생략
        if float(np.sqrt(np.mean(audio ** 2))) < _MIN_RMS:
            return ""
        tokens = self.model.generate(audio[None, :])     # [[id, id, ...]]
        out = self.tokenizer.decode_batch(tokens)
        text = " ".join(out) if isinstance(out, (list, tuple)) else out
        return text.strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio)
