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


def test_feed_block_updates_last_feed_ts():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None,
                             clock=lambda: 123.0)
    rx.recorder = _FakeRecorder()
    rx.feed_block(np.zeros(4, dtype=np.float32))
    assert rx._last_feed_ts == 123.0


def test_maybe_flush_injects_silence_when_feed_stalls_with_pending_partial():
    """발화 partial 이 떠 있는데 공급이 끊기면 → 무음 주입으로 final 을 유도."""
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.recorder = _FakeRecorder()
    rx._partial_last = "오늘은 며칠이야"
    rx._last_feed_ts = 100.0
    # gap 미만 → 아무 것도 안 함
    assert rx._maybe_flush(100.0 + rx._flush_after - 0.1) is False
    assert rx.recorder.fed == []
    # gap 초과 → 무음 주입
    assert rx._maybe_flush(100.0 + rx._flush_after + 0.1) is True
    assert len(rx.recorder.fed) == 1
    pcm_bytes, sr = rx.recorder.fed[0]
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
    assert sr == 16000
    assert pcm.size > 0 and not pcm.any()   # 전부 0(무음)
    # 방금 주입 → 다음 gap 까지 재주입 안 함
    assert rx._maybe_flush(100.0 + rx._flush_after + 0.2) is False


def test_maybe_flush_noop_without_pending_partial():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.recorder = _FakeRecorder()
    rx._partial_last = ""          # 확정 대기 발화 없음
    rx._last_feed_ts = 0.0
    assert rx._maybe_flush(10_000.0) is False
    assert rx.recorder.fed == []


def test_maybe_flush_noop_without_recorder():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx._partial_last = "뭔가"
    assert rx._maybe_flush(10_000.0) is False   # recorder 없음 → 안전
