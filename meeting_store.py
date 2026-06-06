"""회의 기록 로컬 저장(SQLite). 종료 시 1행 저장, 요약은 백그라운드로 나중에 갱신."""
import hashlib
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

    def recent(self, limit: int = 20) -> list:
        """최근 회의 메타 목록(시작 시각 내림차순). 목록 페이지용 — 본문/요약 제외."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, started_at, ended_at FROM meetings "
                "ORDER BY started_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, meeting_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))


def lang_text(lines, lang, room_langs) -> str:
    """트랜스크립트에서 한 언어(lang)의 텍스트만 모아 합침.
    소스언어 = 그 줄의 translations 에 없는 룸 언어(번역 대상에서 빠진 것). 1개로 확정될 때만 source 사용."""
    out = []
    for e in (lines or []):
        tr = e.get("translations") or {}
        missing = [l for l in (room_langs or []) if l not in tr]
        src = missing[0] if len(missing) == 1 else None
        t = (e.get("source") or "") if lang == src else (tr.get(lang) or "")
        if t and t.strip():
            out.append(t)
    return "\n".join(out)


def archive_response(row: dict | None, pw: str, req, *, admin: bool = False) -> dict:
    """저장 행(dict|None) + 평문 pw → archive_response 페이로드.
    admin 이면 비번 검사 생략. 아니면 sha256(pw)==password_hash 여야 ok."""
    if not row:
        return {"req": req, "ok": False}
    if not admin:
        pw_hash = hashlib.sha256((pw or "").encode()).hexdigest()
        if pw_hash != (row.get("password_hash") or ""):
            return {"req": req, "ok": False}
    try:
        transcript = json.loads(row.get("transcript") or "[]")
    except Exception:
        transcript = []
    summary_raw = row.get("summary")
    try:
        summaries = json.loads(summary_raw) if summary_raw else {}
    except Exception:
        summaries = {}
    return {"req": req, "ok": True, "title": row.get("title") or "회의",
            "transcript": transcript, "summaries": summaries}
