# tests/test_meeting_session.py
import asyncio

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
    assert not s.done                      # password 단계 남음
    s.submit("secret")
    assert s.done
    assert s.meta.title == "주간회의"
    assert s.meta.vocabulary == ["신명진", "콘코드", "Jarvis"]
    assert s.meta.password == "secret"


def test_setup_empty_keeps_defaults():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit("")
    s.submit("")
    assert s.meta.title == "회의"
    assert s.meta.vocabulary == ["Jarvis", "민준"]


def test_meeting_id_is_6_hex():
    from live_translate import new_meeting_id
    mid = new_meeting_id()
    assert len(mid) == 6
    assert all(c in "0123456789abcdef" for c in mid)


def test_hash_password_sha256():
    import hashlib
    from live_translate import hash_password
    assert hash_password("hunter2") == hashlib.sha256(b"hunter2").hexdigest()


def test_gen_password_len():
    from live_translate import gen_password
    assert len(gen_password()) == 6


def test_meta_has_new_fields():
    from live_translate import MeetingMeta
    m = MeetingMeta(my_name="민준", title="주간", password="pw")
    assert m.meeting_id == "" and m.started_at == "" and m.password == "pw"


def test_finalize_meta_assigns_ids():
    from live_translate import MeetingMeta
    sess = _sess(meta=MeetingMeta(title="주간"))
    sess._finalize_meta()
    assert len(sess.meta.meeting_id) == 6
    assert len(sess.meta.password) == 6        # 빈 입력 → 자동 생성
    assert sess.meta.started_at != ""


def test_finalize_meta_keeps_given_password():
    from live_translate import MeetingMeta
    sess = _sess(meta=MeetingMeta(password="given"))
    sess._finalize_meta()
    assert sess.meta.password == "given"


def test_record_line_and_translation():
    sess = _sess()
    entry = sess._record_line("hello")
    assert entry["source"] == "hello" and entry["translations"] == {}
    assert sess._transcript == [entry]
    entry["translations"]["ko"] = "안녕"
    assert sess._transcript[0]["translations"]["ko"] == "안녕"


def test_record_shape():
    from live_translate import MeetingMeta, hash_password
    sess = _sess(meta=MeetingMeta(title="주간", password="pw", languages=["ko", "en"]))
    sess.meta.meeting_id = "abc123"
    sess.meta.started_at = "2026-06-06T10:00:00"
    sess._record_line("hi")
    rec = sess.record()
    assert rec["id"] == "abc123"
    assert rec["title"] == "주간"
    assert rec["languages"] == ["ko", "en"]
    assert rec["transcript"][0]["source"] == "hi"
    assert rec["transcript"][0]["translations"] == {}


def test_stop_awaits_pending_translations():
    """회귀: stop() 이 진행 중 번역 태스크 완료를 기다려야 record() 가 마지막 줄까지 담는다."""
    async def run():
        sess = _sess()
        done = []

        async def slow():
            await asyncio.sleep(0.01)
            done.append(True)

        t = asyncio.create_task(slow())
        sess._tx_tasks.add(t)
        await sess.stop()
        assert done == [True]
    asyncio.run(run())


def test_setup_password_empty_stays_blank():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit(""); s.submit(""); s.submit("")
    assert s.done
    assert s.meta.password == ""   # 빈 입력 → 세션 시작 시 자동 생성
