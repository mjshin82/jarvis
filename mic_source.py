# mic_source.py
"""마이크 입력 소스 추상화.

Microphone 이 소비하는 512-샘플 float32 블록의 공급원을 분리한다:
  - LocalMicSource  : sounddevice InputStream (기본)
  - RemoteMicSource : 외부에서 주입된 Int16 PCM → float32 → 512 재청크
  - MicRouter       : 활성 소스 선택(자동 전환 + 수동 오버라이드)

모든 소스는 동일한 sink(block: np.ndarray)->None 으로 블록을 흘려보낸다.
"""
import queue
import time

import numpy as np

import config


class RemoteMicSource:
    """주입된 16kHz mono Int16 PCM 을 float32 512-블록으로 재청크해 sink 로 방출."""

    def __init__(self, sink):
        self._sink = sink
        self._buf = np.empty(0, dtype=np.float32)

    def feed(self, pcm_bytes: bytes) -> None:
        """Int16 little-endian PCM 바이트를 받아 누적·재청크."""
        samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, samples])
        bs = config.BLOCK_SIZE
        while len(self._buf) >= bs:
            self._sink(np.ascontiguousarray(self._buf[:bs]))
            self._buf = self._buf[bs:]

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.float32)
