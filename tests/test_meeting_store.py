import json
from meeting_store import MeetingStore


def _rec(mid="abc123"):
    return {
        "id": mid, "password_hash": "h", "title": "주간",
        "started_at": "2026-06-06T10:00:00", "ended_at": "2026-06-06T10:30:00",
        "transcript": [{"ts": "t", "source": "hi", "ko": "안녕", "en": ""}],
    }


def test_save_and_read(tmp_path):
    db = str(tmp_path / "m.db")
    store = MeetingStore(db)
    store.save(_rec())
    row = store.get("abc123")
    assert row["title"] == "주간"
    assert row["summary"] is None
    assert json.loads(row["transcript"])[0]["source"] == "hi"


def test_set_summary(tmp_path):
    db = str(tmp_path / "m.db")
    store = MeetingStore(db)
    store.save(_rec())
    store.set_summary("abc123", "요약본")
    assert store.get("abc123")["summary"] == "요약본"


def test_save_is_idempotent_on_id(tmp_path):
    db = str(tmp_path / "m.db")
    store = MeetingStore(db)
    store.save(_rec())
    store.save({**_rec(), "title": "수정"})   # 같은 id → 덮어쓰기(REPLACE)
    assert store.get("abc123")["title"] == "수정"


def test_archive_response_ok(tmp_path):
    import hashlib
    from meeting_store import MeetingStore, archive_response
    store = MeetingStore(str(tmp_path / "m.db"))
    store.save({
        "id": "m1", "password_hash": hashlib.sha256(b"pw").hexdigest(),
        "title": "주간", "started_at": "s", "ended_at": "e",
        "transcript": [{"ts": "t", "source": "hi", "src_lang": "", "translations": {"en": "hi"}}],
    })
    store.set_summary("m1", json.dumps({"ko": "요약본"}, ensure_ascii=False))
    r = archive_response(store.get("m1"), "pw", 7)
    assert r["ok"] is True and r["req"] == 7 and r["title"] == "주간"
    assert r["transcript"][0]["source"] == "hi"
    assert r["summaries"] == {"ko": "요약본"}


def test_archive_response_bad_pw_or_missing(tmp_path):
    import hashlib
    from meeting_store import MeetingStore, archive_response
    store = MeetingStore(str(tmp_path / "m.db"))
    store.save({"id": "m1", "password_hash": hashlib.sha256(b"pw").hexdigest(),
                "title": "t", "started_at": "", "ended_at": "", "transcript": []})
    assert archive_response(store.get("m1"), "wrong", 1) == {"req": 1, "ok": False}
    assert archive_response(None, "x", 2) == {"req": 2, "ok": False}


def test_archive_response_null_summary(tmp_path):
    import hashlib
    from meeting_store import MeetingStore, archive_response
    store = MeetingStore(str(tmp_path / "m.db"))
    store.save({"id": "m1", "password_hash": hashlib.sha256(b"pw").hexdigest(),
                "title": "t", "started_at": "", "ended_at": "", "transcript": []})
    r = archive_response(store.get("m1"), "pw", 3)   # summary 아직 NULL
    assert r["ok"] is True and r["summaries"] == {}


def test_recent_orders_desc_and_limits(tmp_path):
    from meeting_store import MeetingStore
    store = MeetingStore(str(tmp_path / "m.db"))
    for i, ts in enumerate(["2026-06-01T10:00:00", "2026-06-03T10:00:00", "2026-06-02T10:00:00"]):
        store.save({"id": f"m{i}", "password_hash": "h", "title": f"T{i}",
                    "started_at": ts, "ended_at": ts, "transcript": []})
    rows = store.recent(2)
    assert [r["id"] for r in rows] == ["m1", "m2"]      # 최신순(06-03, 06-02)
    assert set(rows[0].keys()) == {"id", "title", "started_at", "ended_at"}


def test_archive_response_admin_bypasses_pw(tmp_path):
    import hashlib
    from meeting_store import MeetingStore, archive_response
    store = MeetingStore(str(tmp_path / "m.db"))
    store.save({"id": "m1", "password_hash": hashlib.sha256(b"pw").hexdigest(),
                "title": "주간", "started_at": "s", "ended_at": "e",
                "transcript": [{"ts": "t", "source": "hi", "translations": {}}]})
    r = archive_response(store.get("m1"), "WRONG", 9, admin=True)
    assert r["ok"] is True and r["title"] == "주간"
    assert archive_response(None, "x", 1, admin=True) == {"req": 1, "ok": False}
