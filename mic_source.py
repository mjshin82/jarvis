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
        """Int16 little-endian PCM 바이트를 받아 누적·재청크.
        네트워크 프레임이라 홀수 바이트(부분 샘플)는 버린다."""
        if len(pcm_bytes) % 2:
            pcm_bytes = pcm_bytes[:-1]
        samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, samples])
        bs = config.BLOCK_SIZE
        while len(self._buf) >= bs:
            self._sink(np.ascontiguousarray(self._buf[:bs]))
            self._buf = self._buf[bs:]
        self._buf = self._buf.copy()   # 뷰가 큰 버퍼를 잡고 있지 않도록 압축

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.float32)


class MicRouter:
    """활성 마이크 소스를 골라 그 블록만 block_queue 로 흘린다.

    mode: 'auto'  — 원격 프레임이 오면 remote, idle 초과 시 local 복귀
          'local' — 시스템 마이크 강제
          'remote'— 원격 강제
    """

    def __init__(self, block_queue, *, local=None, remote=None, clock=time.monotonic):
        self._q = block_queue
        self._clock = clock
        self._mode = "auto"
        self._active = "local"
        self._last_remote = 0.0
        self.local = local if local is not None else LocalMicSource(sink=self._sink_local)
        self.remote = remote if remote is not None else RemoteMicSource(sink=self._sink_remote)

    # --- sink (소스가 블록을 흘려보낼 때 호출) ---
    def _sink_local(self, block):
        if self._active == "local":
            self._q.put(block)

    def _sink_remote(self, block):
        if self._active == "remote":
            self._q.put(block)

    # --- 라이프사이클 ---
    def start(self):
        self.local.start()

    def stop(self):
        self.local.stop()

    def pause_local(self):
        self.local.stop()

    def resume_local(self):
        self.local.start()

    # --- 원격 수신 진입점 (RemoteMicReceiver 가 호출) ---
    def on_remote_frame(self, pcm_bytes):
        self.note_remote_activity(self._clock())
        self.remote.feed(pcm_bytes)

    # --- 전환 로직 ---
    def note_remote_activity(self, now):
        self._last_remote = now
        if self._mode == "auto" and self._active != "remote":
            self._switch("remote")

    def check_idle(self, now):
        if self._mode == "auto" and self._active == "remote":
            if now - self._last_remote > config.REMOTE_MIC_IDLE_S:
                self._switch("local")

    def set_override(self, mode):
        self._mode = mode
        if mode == "local":
            self._switch("local")
        elif mode == "remote":
            self._switch("remote")
        # 'auto': 다음 활동/idle 검사를 따른다

    def _switch(self, target):
        if self._active == target:
            return
        self._active = target
        # 소스 간 오디오 혼입 방지: 큐 잔여 비우고 원격 재청크 버퍼 리셋
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self.remote.reset()

    async def run_idle_monitor(self):
        import asyncio
        while True:
            await asyncio.sleep(0.5)
            self.check_idle(self._clock())
