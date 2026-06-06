"""회의 기록 로컬 저장(SQLite). 종료 시 1행 저장, 요약은 백그라운드로 나중에 갱신."""
import json
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id            TEXT PRIMARY KEY,
    password_hash TEXT,
    title         TEXT,
    started_at    TEXT,
    ended_at      TEXT,
    transcript    TEXT,
    summary       TEXT
)
"""


class MeetingStore:
    """meetings.db 래퍼. 스레드에서 호출될 수 있어 매 연산마다 짧게 connect."""

    def __init__(self, path: str = "meetings.db"):
        self.path = path
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, record: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO meetings "
                "(id, password_hash, title, started_at, ended_at, transcript, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    record["id"], record.get("password_hash"), record.get("title"),
                    record.get("started_at"), record.get("ended_at"),
                    json.dumps(record.get("transcript") or [], ensure_ascii=False),
                ),
            )

    def set_summary(self, meeting_id: str, summary: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE meetings SET summary=? WHERE id=?", (summary, meeting_id))

    def get(self, meeting_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        return dict(row) if row else None
