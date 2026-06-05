# tests/test_meeting_session.py
import numpy as np

from live_translate import MeetingSession


def _sess(**kw):
    return MeetingSession(log=lambda *_: None, set_status=lambda *_: None, llm=None, **kw)


def test_feed_block_converts_float32_to_int16_bytes():
    sess = _sess()
    calls = []

    class FakeRec:
        def feed_audio(self, chunk, sr): calls.append((chunk, sr))

    sess.recorder = FakeRec()
    sess.feed_block(np.array([0.5, -0.5, 0.0, 1.0], dtype=np.float32))
    assert len(calls) == 1
    chunk, sr = calls[0]
    assert sr == 16000
    arr = np.frombuffer(chunk, dtype="<i2")
    assert arr[0] == 16383      # 0.5 * 32767 → 16383 (절삭)
    assert arr[1] == -16383
    assert arr[2] == 0
    assert arr[3] == 32767      # 1.0 클립


def test_feed_block_noop_without_recorder():
    sess = _sess()
    sess.recorder = None
    sess.feed_block(np.zeros(4, dtype=np.float32))   # 예외 없이 무시


def test_no_use_remote_param():
    import pytest
    with pytest.raises(TypeError):
        _sess(use_remote=True)
