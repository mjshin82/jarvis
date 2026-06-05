# tests/test_meeting_session.py
from live_translate import MeetingSession


def _sess(**kw):
    return MeetingSession(log=lambda *_: None, set_status=lambda *_: None, llm=None, **kw)


def test_use_remote_flag_stored():
    assert _sess(use_remote=True).use_remote is True
    assert _sess().use_remote is False


def test_feed_remote_calls_recorder_feed_audio():
    sess = _sess(use_remote=True)
    calls = []

    class FakeRec:
        def feed_audio(self, chunk, sr): calls.append((chunk, sr))

    sess.recorder = FakeRec()
    sess.feed_remote(b"\x01\x02\x03\x04")
    assert calls == [(b"\x01\x02\x03\x04", 16000)]


def test_feed_remote_noop_without_recorder():
    sess = _sess(use_remote=True)
    sess.recorder = None
    sess.feed_remote(b"\x01\x02")   # 예외 없이 무시
