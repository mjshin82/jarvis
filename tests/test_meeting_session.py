# tests/test_meeting_session.py
import numpy as np

from live_translate import MeetingSession


def _sess(**kw):
    return MeetingSession(log=lambda *_: None, set_status=lambda *_: None, llm=None, **kw)


def test_feed_block_converts_float32_to_int16_bytes():
    sess = _sess()
    calls = []

    class FakeRT:
        def feed_pcm16(self, chunk): calls.append(chunk)

    sess._rt = FakeRT()
    sess.feed_block(np.array([0.5, -0.5, 0.0, 1.0], dtype=np.float32))
    assert len(calls) == 1
    arr = np.frombuffer(calls[0], dtype="<i2")
    assert arr[0] == 16383
    assert arr[1] == -16383
    assert arr[2] == 0
    assert arr[3] == 32767


def test_feed_block_noop_without_recorder():
    sess = _sess()
    sess._stt = None
    sess._rt = None
    sess.feed_block(np.zeros(4, dtype=np.float32))   # 예외 없이 무시


def test_no_use_remote_param():
    import pytest
    with pytest.raises(TypeError):
        _sess(use_remote=True)
