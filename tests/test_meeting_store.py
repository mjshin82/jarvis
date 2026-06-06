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
