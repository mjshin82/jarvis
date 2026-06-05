# tests/test_microphone_events.py
import asyncio

import numpy as np

import config
from audio_io import Microphone


class FakeVAD:
    """N번째 호출에 start, M번째에 end 이벤트를 낸다."""
    def __init__(self, start_at, end_at):
        self.n = 0
        self.start_at = start_at
        self.end_at = end_at

    def __call__(self, block):
        self.n += 1
        if self.n == self.start_at:
            return {"start": self.n}
        if self.n == self.end_at:
            return {"end": self.n}
        return None

    def reset_states(self):
        pass


class FakeLocal:
    """LocalMicSource 대체 — 실제 sounddevice 장치를 열지 않는다."""
    def start(self):
        pass

    def stop(self):
        pass


def test_remote_frames_become_utterance_via_events():
    vad = FakeVAD(start_at=2, end_at=4)
    mic = Microphone(vad_default=vad, vad_translate=vad)
    mic.router.local = FakeLocal()      # 하드웨어 미개방
    mic.router.set_override("remote")   # 원격 강제

    async def main():
        events = []

        async def consume():
            async for kind, audio in mic.events(wake_detect=None, is_speaking=lambda: False):
                events.append((kind, audio))
                if kind == "utterance":
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        # 512 샘플(=Int16 1024바이트) 프레임 5개 주입
        frame = (np.ones(512, dtype=np.int16) * 1000).tobytes()
        for _ in range(5):
            mic.router.on_remote_frame(frame)
        await asyncio.wait_for(task, timeout=3)
        kinds = [k for k, _ in events]
        assert "start" in kinds and "utterance" in kinds
        utt = next(a for k, a in events if k == "utterance")
        assert utt.dtype == np.float32 and utt.ndim == 1

    asyncio.run(main())
