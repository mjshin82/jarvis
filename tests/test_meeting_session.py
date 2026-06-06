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


def test_setup_defaults():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    assert s.meta.title == "회의"
    assert s.meta.vocabulary == ["Jarvis", "민준"]
    assert not s.done


def test_setup_submit_title_and_vocab():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit("주간회의")
    s.submit("신명진, 콘코드, Jarvis")
    assert s.done
    assert s.meta.title == "주간회의"
    assert s.meta.vocabulary == ["신명진", "콘코드", "Jarvis"]


def test_setup_empty_keeps_defaults():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit("")
    s.submit("")
    assert s.meta.title == "회의"
    assert s.meta.vocabulary == ["Jarvis", "민준"]
