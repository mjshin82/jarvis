"""openWakeWord 기반 'Hey Jarvis' 호출어 감지 (로컬, ONNX).

항상 켜져 있어도 가볍고, 일반 음성/에코에는 거의 오발동하지 않는다.
마이크 블록(float32 16kHz)을 80ms(1280샘플) 청크로 모아 모델에 흘려보낸다.
감지는 동기/CPU 바운드지만 매우 가벼워 이벤트 루프에서 바로 호출해도 무방.
"""
import time

import numpy as np
import openwakeword
import openwakeword.utils
from openwakeword.model import Model

import config


class WakeWord:
    def __init__(self):
        # 최초 1회 모델 다운로드(이미 받았으면 빠르게 통과)
        try:
            openwakeword.utils.download_models([config.WAKE_MODEL])
        except Exception as e:  # 네트워크 없이 캐시만으로도 동작
            print(f"[wake] download skip: {e}")

        self.model = Model(
            wakeword_models=[config.WAKE_MODEL], inference_framework="onnx"
        )
        self.key = config.WAKE_MODEL
        self.threshold = config.WAKE_THRESHOLD
        self._buf = np.zeros(0, dtype=np.int16)
        self._chunk = 1280  # 80ms @ 16kHz (openWakeWord 권장)
        self._last_fire = 0.0
        self._cooldown = config.WAKE_COOLDOWN_S
        print(f"[wake] '{self.key}' 대기 (threshold={self.threshold})")

    def detect(self, block_f32: np.ndarray) -> bool:
        """마이크 블록 하나를 먹이고, 이번에 호출어가 감지됐으면 True."""
        pcm16 = (np.clip(block_f32, -1.0, 1.0) * 32767).astype(np.int16)
        self._buf = np.concatenate([self._buf, pcm16])

        while len(self._buf) >= self._chunk:
            chunk = self._buf[: self._chunk]
            self._buf = self._buf[self._chunk :]
            scores = self.model.predict(chunk)
            if scores.get(self.key, 0.0) >= self.threshold:
                now = time.monotonic()
                if now - self._last_fire >= self._cooldown:
                    self._last_fire = now
                    self.model.reset()          # 연속 프레임 중복 발동 방지
                    self._buf = np.zeros(0, dtype=np.int16)
                    return True
        return False
