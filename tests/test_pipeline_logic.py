# tests/test_pipeline_logic.py
"""Microphone.events() 의 wake/utterance 파이프라인 로직 검증.
원격 소스로 결정적 프레임을 주입하고 FakeVAD 로 발화 경계를 만든다 (silero 미로드)."""
import asyncio

import numpy as np

import audio_io
from audio_io import Microphone

S = {"start": 0.9}
E = {"end": 1.0}
N = None


class FakeVAD:
    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def __call__(self, b):
        ev = self.script[self.i] if self.i < len(self.script) else None
        self.i += 1
        return ev

    def reset_states(self):
        pass


class FakeLocal:
    def start(self):
        pass

    def stop(self):
        pass


def _frame():
    # 512 int16 샘플 = 재청크 후 정확히 BLOCK_SIZE 블록 1개
    return np.zeros(audio_io.config.BLOCK_SIZE, dtype=np.int16).tobytes()


async def collect(mic, script, wake_block=None, speaking=False, expect=2):
    mic._vad_default = FakeVAD(script)
    mic._vad_translate = mic._vad_default
    mic.router.local = FakeLocal()
    mic.router.set_override("remote")
    calls = {"i": 0}

    def wake(b):
        calls["i"] += 1
        return wake_block is not None and calls["i"] == wake_block

    out = []

    async def consume():
        async for kind, _ in mic.events(wake_detect=wake, is_speaking=lambda: speaking):
            out.append(kind)
            if len(out) >= expect:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    for _ in range(len(script)):
        mic.router.on_remote_frame(_frame())
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()
        raise AssertionError(f"events 수집 타임아웃 — 수집된 이벤트: {out}")
    return out


def test_wake_discards_pending_utterance():
    async def main():
        mic = _mic()
        out = await collect(mic, [S, N, E, N], wake_block=2)
        assert "wake" in out and "utterance" not in out
    asyncio.run(main())


def test_normal_capture():
    async def main():
        mic = _mic()
        out = await collect(mic, [S, N, E])
        assert out == ["start", "utterance"]
    asyncio.run(main())


def _mic():
    # FakeVAD 를 collect() 에서 주입하므로 여기서는 placeholder VAD 로 생성(silero 미로드)
    placeholder = FakeVAD([])
    return Microphone(vad_default=placeholder, vad_translate=placeholder)
