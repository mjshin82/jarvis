# tests/test_streaming_stt.py
import asyncio
import numpy as np
from streaming_stt import StreamingRecognizer


class _FakeRecorder:
    def __init__(self):
        self.fed = []
    def feed_audio(self, pcm, sr):
        self.fed.append((pcm, sr))


def test_feed_block_converts_to_int16_pcm():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.recorder = _FakeRecorder()
    block = np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)
    rx.feed_block(block)
    assert len(rx.recorder.fed) == 1
    pcm_bytes, sr = rx.recorder.fed[0]
    assert sr == 16000
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
    assert pcm[0] == 0 and pcm[1] == 32767 and pcm[2] == -32767


def test_feed_block_noop_without_recorder():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.feed_block(np.zeros(4, dtype=np.float32))   # recorder is None → no error


def test_partial_dedup_and_strip():
    seen = []
    rx = StreamingRecognizer(on_partial=lambda t: seen.append(t), on_final=lambda t: None)
    rx._on_partial("안녕")
    rx._on_partial("안녕")          # dup → skip
    rx._on_partial("  안녕하세요 ")  # strip → new
    rx._on_partial("")             # empty → skip
    assert seen == ["안녕", "안녕하세요"]


def test_final_dispatch_via_queue():
    got = []
    async def run():
        rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: got.append(t))
        rx._loop = asyncio.get_running_loop()
        rx._final_q = asyncio.Queue()
        consumer = asyncio.create_task(rx._consume_finals())
        await rx._final_q.put("최종 텍스트")
        await asyncio.sleep(0.05)
        await rx._final_q.put(None)   # 종료 센티넬
        await consumer
    asyncio.run(run())
    assert got == ["최종 텍스트"]
