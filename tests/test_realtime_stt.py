import asyncio
import numpy as np
from realtime_stt import RealtimeSTTAdapter, to_pcm16


class _FakeRecorder:
    def __init__(self): self.fed = []
    def feed_audio(self, pcm, sr): self.fed.append((pcm, sr))


def _adapter(**over):
    deps = dict(on_partial=lambda t: None, on_final=lambda t: None)
    deps.update(over)
    return RealtimeSTTAdapter(**deps)


def test_to_pcm16():
    pcm = np.frombuffer(to_pcm16(np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)), dtype="<i2")
    assert pcm[0] == 0 and pcm[1] == 32767 and pcm[2] == -32767


def test_partial_dedup_and_strip():
    seen = []
    a = _adapter(on_partial=lambda t: seen.append(t))
    a._on_partial("안녕"); a._on_partial("안녕"); a._on_partial("  안녕하세요 "); a._on_partial("")
    assert seen == ["안녕", "안녕하세요"]


def test_feed_block_converts_and_updates_ts():
    a = _adapter(clock=lambda: 123.0)
    a.recorder = _FakeRecorder()
    a.feed_block(np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32))
    assert len(a.recorder.fed) == 1
    pcm, sr = a.recorder.fed[0]
    assert sr == 16000
    assert np.frombuffer(pcm, dtype="<i2")[1] == 32767
    assert a._last_feed_ts == 123.0


def test_feed_block_noop_without_recorder():
    _adapter().feed_block(np.zeros(4, dtype=np.float32))   # recorder None → 무시(예외 없음)


def test_dispatch_final_resets_partial_and_calls_on_final():
    got = []
    a = _adapter(on_final=lambda t: got.append(t))
    a._partial_last = "진행중"
    a._dispatch_final("최종")
    assert got == ["최종"] and a._partial_last == ""
    a._partial_last = "x"
    a._dispatch_final("")              # 빈 텍스트 → on_final 미호출, partial 리셋
    assert got == ["최종"] and a._partial_last == ""


def test_maybe_flush_injects_silence_when_stalled():
    a = _adapter(flush_after=1.2)
    a.recorder = _FakeRecorder()
    a._partial_last = "오늘은 며칠이야"
    a._last_feed_ts = 100.0
    assert a._maybe_flush(100.0 + 1.1) is False
    assert a.recorder.fed == []
    assert a._maybe_flush(100.0 + 1.3) is True
    pcm, sr = a.recorder.fed[0]
    assert sr == 16000
    arr = np.frombuffer(pcm, dtype="<i2")
    assert arr.size > 0 and not arr.any()
    assert a._maybe_flush(100.0 + 1.4) is False   # 방금 주입 → gap 리셋, 재주입 안 함


def test_maybe_flush_noop_without_pending_partial():
    a = _adapter()
    a.recorder = _FakeRecorder()
    a._partial_last = ""
    assert a._maybe_flush(10_000.0) is False
