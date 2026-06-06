# 회의 ID + 기록 저장 (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의마다 유니크 ID 발급, 회의 중 원문+번역 트랜스크립트 누적, 종료 시 SQLite 저장 + 백그라운드 LLM 요약 (순수 jarvis, 웹 변경 없음).

**Architecture:** `MeetingMeta` 에 meeting_id/password/started_at 추가 + 모듈 헬퍼. `MeetingSession` 이 시작 시 메타 확정, 회의 중 `_transcript` 누적, 종료 시 `record()` 반환. 신규 `meeting_store.py`(sqlite3) 가 저장/요약갱신, `llm.summarize()` 원샷 요약. `conversation` 의 새 `save_meeting` 포트를 `main` 이 "즉시 저장 → 백그라운드 요약" 으로 와이어링.

**Tech Stack:** Python 3.11 표준 sqlite3/hashlib/secrets + pytest(tmp_path). 신규 의존성 없음.

**스펙:** `docs/superpowers/specs/2026-06-06-meeting-id-password-record-design.md` (Phase 1)

---

## Task 1: MeetingMeta 필드 + 모듈 헬퍼 (live_translate.py)

**Files:** Modify `live_translate.py`; Test `tests/test_meeting_session.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_session.py` 끝에

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: FAIL (new_meeting_id 등 미정의, MeetingMeta 필드 없음)

- [ ] **Step 3: 구현** — `live_translate.py`

(a) import 블록(line 27 `from realtime_stt ...` 바로 아래)에 추가:
```python
import hashlib
import secrets
import time
from datetime import datetime
```

(b) `MeetingMeta` 에 필드 추가(`vocabulary` 줄 아래, `key` 프로퍼티 위):
```python
    meeting_id: str = ""     # 6자리 hex, 회의 시작 시 발급
    password: str = ""       # 평문(입력/자동). 표시·해시용, DB 미저장
    started_at: str = ""     # ISO8601, 회의 시작 시각
```

(c) `_META_STEPS = (...)` 정의 **위**에 모듈 헬퍼 추가:
```python
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_meeting_id() -> str:
    """회의별 로컬 유니크 ID. 생성시간 md5 앞 6자리(소문자 hex)."""
    return hashlib.md5(repr(time.time()).encode()).hexdigest()[:6]


def gen_password() -> str:
    """비번 미입력 시 자동 생성(6자리 hex)."""
    return secrets.token_hex(3)


def hash_password(pw: str) -> str:
    return hashlib.sha256((pw or "").encode()).hexdigest()
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**
```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "feat(meeting): MeetingMeta meeting_id/password/started_at + id/hash 헬퍼

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: MeetingSession 메타 확정 + 트랜스크립트 누적 + record() (live_translate.py)

**Files:** Modify `live_translate.py`; Test `tests/test_meeting_session.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_session.py` 끝에

```python
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
    assert entry["source"] == "hello" and entry["ko"] == "" and entry["en"] == ""
    assert sess._transcript == [entry]
    entry["ko"] = "안녕"
    assert sess._transcript[0]["ko"] == "안녕"


def test_record_shape():
    from live_translate import MeetingMeta, hash_password
    sess = _sess(meta=MeetingMeta(title="주간", password="pw"))
    sess.meta.meeting_id = "abc123"
    sess.meta.started_at = "2026-06-06T10:00:00"
    sess._record_line("hi")
    rec = sess.record()
    assert rec["id"] == "abc123"
    assert rec["password_hash"] == hash_password("pw")
    assert rec["title"] == "주간"
    assert rec["started_at"] == "2026-06-06T10:00:00"
    assert rec["ended_at"] != ""
    assert rec["transcript"][0]["source"] == "hi"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: FAIL (`_finalize_meta`/`_record_line`/`record`/`_transcript` 없음)

- [ ] **Step 3: 구현** — `live_translate.py` `MeetingSession`

(a) `__init__` 의 `self._tx_label = ""` 줄 아래에 추가:
```python
        self._transcript: list[dict] = []    # [{ts, source, ko, en}] — 회의 기록
```

(b) 메서드 추가(`def feed_block` 위 등 `MeetingSession` 내부 적당한 위치):
```python
    def _finalize_meta(self) -> None:
        """회의 시작 시 메타 확정 — id 발급, 비번 미입력 시 자동 생성, 시작 시각 기록."""
        if not self.meta.meeting_id:
            self.meta.meeting_id = new_meeting_id()
        if not self.meta.password:
            self.meta.password = gen_password()
        self.meta.started_at = now_iso()

    def _record_line(self, source: str) -> dict:
        """확정 원문 1줄을 트랜스크립트에 추가하고 entry 반환(번역은 나중에 채움)."""
        entry = {"ts": now_iso(), "source": source, "ko": "", "en": ""}
        self._transcript.append(entry)
        return entry

    def record(self) -> dict:
        """종료 시 저장할 회의 기록. (stop() 후 호출)"""
        return {
            "id": self.meta.meeting_id,
            "password_hash": hash_password(self.meta.password),
            "title": self.meta.title or "회의",
            "started_at": self.meta.started_at,
            "ended_at": now_iso(),
            "transcript": list(self._transcript),
        }
```

(c) `start()` 맨 앞(`self._loop = asyncio.get_running_loop()` 위)에 추가:
```python
        self._finalize_meta()
        self._transcript = []
```

(d) `start()` 의 시작 로그 `self.log(f"🎤 회의 시작: {self.meta.title or '회의'}")` 를 교체:
```python
        self.log(f"🎤 회의 시작: {self.meta.title or '회의'} (ID {self.meta.meeting_id})")
```

(e) `_consume_finals` 의 `asyncio.create_task(self._translate_bg(text))` 를 교체:
```python
            entry = self._record_line(text)
            asyncio.create_task(self._translate_bg(text, entry))
```

(f) `_translate_bg` 시그니처/말미 교체:
```python
    async def _translate_bg(self, text: str, entry: dict | None = None):
```
그리고 끝부분 `if out:` 블록을 교체:
```python
        if out:
            kind = "translation_en" if coach.is_korean(text) else "translation_ko"
            if entry is not None:
                entry["en" if kind == "translation_en" else "ko"] = out
            self._emit(kind, out)
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "feat(meeting): 메타 확정 + 원문/번역 트랜스크립트 누적 + record()

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: meeting_store.py — SQLite 저장 (신규)

**Files:** Create `meeting_store.py`; Test `tests/test_meeting_store.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_meeting_store.py` (신규)

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: FAIL (meeting_store 모듈 없음)

- [ ] **Step 3: 구현** — `meeting_store.py` (신규)

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**
```bash
git add meeting_store.py tests/test_meeting_store.py
git commit -m "feat(meeting): MeetingStore — SQLite 회의 기록 저장/요약갱신

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: llm.summarize() 원샷 요약 (llm.py)

**Files:** Modify `llm.py`; Test `tests/test_llm_summarize.py` (신규)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_llm_summarize.py` (신규)

```python
import asyncio
from llm import LLM


def test_summarize_mock_returns_empty(monkeypatch):
    monkeypatch.setattr("config.LLM_BACKEND", "mock", raising=False)
    llm = LLM()
    llm._mock = True
    out = asyncio.run(llm.summarize("아무 회의 내용"))
    assert out == ""


def test_summarize_calls_client():
    llm = LLM()
    llm._mock = False
    captured = {}

    class FakeMsg:
        content = "요약본"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kw):
            captured.update(kw)
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    llm.client = FakeClient()
    llm.model = "m"
    llm.extra = {}
    out = asyncio.run(llm.summarize("회의 원문"))
    assert out == "요약본"
    assert captured["model"] == "m"
    assert "회의 원문" in captured["messages"][-1]["content"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q`
Expected: FAIL (summarize 미정의)

- [ ] **Step 3: 구현** — `llm.py` 에 메서드 추가(클래스 `LLM` 내부, `respond` 근처)

```python
    async def summarize(self, text: str) -> str:
        """회의 트랜스크립트 1회 요약. 현재 백엔드 사용. mock/미설정이면 빈 문자열."""
        if self._mock or self.client is None or not (text or "").strip():
            return ""
        messages = [
            {"role": "system", "content":
             "다음 회의 대화를 한국어로 간결히 요약하라. 주요 논의·결정·할 일 위주로 불릿."},
            {"role": "user", "content": text},
        ]
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, extra_body=self.extra,
        )
        return (resp.choices[0].message.content or "").strip()
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add llm.py tests/test_llm_summarize.py
git commit -m "feat(llm): summarize() — 회의 트랜스크립트 원샷 요약

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: conversation.save_meeting 포트 + 종료 시 저장 (conversation.py)

**Files:** Modify `conversation.py`, `tests/test_conversation.py`

- [ ] **Step 1: 테스트 — FakeSession.record() 추가 + 신규 테스트** — `tests/test_conversation.py`

(a) `class FakeSession` 에 `record` 메서드 추가(`def feed_block` 줄 아래):
```python
    def record(self): return {"id": "fake", "transcript": []}
```
(b) 파일 끝에 추가:
```python
def test_meeting_end_saves_record():
    async def run():
        saved = []
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess,
                            save_meeting=lambda r: saved.append(r))
        await c.start_meeting()
        await c.stop_meeting()
        assert saved == [{"id": "fake", "transcript": []}]
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL (save_meeting 미지원 → 저장 안 됨)

- [ ] **Step 3: 구현** — `conversation.py`

(a) `__init__` 시그니처에 포트 추가 — `persist_mode=lambda m: None,` 줄 아래:
```python
                 save_meeting=lambda record: None,
```
(b) `__init__` 본문 `self.persist_mode = persist_mode` 아래:
```python
        self.save_meeting = save_meeting
```
(c) `_teardown` 의 MEETING 분기에서 `meeting_session.stop()` 직후, `self.mic.restore_mode(...)` 줄 **위**에 저장 추가:
```python
                try:
                    self.save_meeting(self.meeting_session.record())
                except Exception as e:
                    self.log(f"회의 기록 저장 실패: {e}")
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): save_meeting 포트 + 회의 종료 시 기록 저장

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: main.py 와이어링 + .gitignore (main.py)

**Files:** Modify `main.py`, `.gitignore`. 검증: import + pytest.

- [ ] **Step 1: .gitignore 에 DB 추가** — `.jarvis_state.json` 줄 아래에:
```
meetings.db
```

- [ ] **Step 2: main.py import 추가** — `from llm import LLM` 줄 아래:
```python
from meeting_store import MeetingStore
```

- [ ] **Step 3: store + 저장 콜백 정의 + 컨트롤러 배선** — `controller = ConversationController(` **위**에 추가:
```python
    store = MeetingStore("meetings.db")

    def _save_meeting(record):
        """종료 시 즉시 저장 → 트랜스크립트 있으면 백그라운드 요약 후 갱신."""
        async def _run():
            try:
                await asyncio.to_thread(store.save, record)
            except Exception as e:
                console.log(f"회의 저장 실패: {e}")
                return
            lines = record.get("transcript") or []
            if not lines:
                return
            text = "\n".join(
                (e.get("source") or "") +
                (f" / {e.get('ko') or e.get('en')}" if (e.get("ko") or e.get("en")) else "")
                for e in lines
            )
            try:
                summary = await llm.summarize(text)
            except Exception as e:
                console.log(f"회의 요약 실패: {e}")
                return
            if summary:
                try:
                    await asyncio.to_thread(store.set_summary, record["id"], summary)
                    console.log(f"📝 회의 요약 저장됨 (ID {record['id']})")
                except Exception as e:
                    console.log(f"요약 저장 실패: {e}")
        asyncio.create_task(_run())
```
그리고 `ConversationController(...)` 호출의 `persist_mode=runtime_state.save_mode,` 줄 아래에 인자 추가:
```python
        save_meeting=_save_meeting,
```

- [ ] **Step 4: after_meeting_start 로그에 meeting_id + URL** — `_after_meeting_start` 의 자막 URL 로그 교체

기존:
```python
            view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
            console.log(f"🌐 자막: {view_base}/{sess.meta.key}/meeting")
            web_pub.emit("meeting_title", sess.meta.title)
```
교체:
```python
            view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
            console.log(f"🔑 회의 ID: {sess.meta.meeting_id}")
            console.log(f"🌐 자막: {view_base}/{sess.meta.key}/meeting/{sess.meta.meeting_id}")
            web_pub.emit("meeting_title", sess.meta.title)
```

- [ ] **Step 5: 검증**
```bash
.venv/bin/python -c "import main, live_translate, llm, meeting_store, conversation; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 6: 커밋**
```bash
git add main.py .gitignore
git commit -m "feat(meeting): main 와이어링 — 종료 시 저장+백그라운드 요약, 회의 URL 에 ID

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, live_translate, llm, meeting_store, conversation; print('import ok')"
.venv/bin/python -m pytest -q
git status --porcelain
```
Expected: `import ok`, 전체 통과, `meetings.db` 는 git 추적 안 됨(상태 깨끗).

- [ ] **Step 2: 수동 (재시작 후)**
- `/meet` → 회의 시작 시 콘솔에 `🔑 회의 ID: {6자리}` + `🌐 자막: .../{room}/meeting/{id}` 표시.
- 몇 마디 발화 후 `/stop` → `meetings.db` 에 행 생성(`id/title/started_at/ended_at/transcript`), 잠시 후 `📝 회의 요약 저장됨` + summary 채워짐.
- `sqlite3 meetings.db "select id,title,summary from meetings;"` 로 확인.

---

## 비고
- Phase 2(비번 게이트: 폼 입력·DO 검증·viewer 비번 페이지)는 별도 플랜. 이 단계는 비번을 자동 생성·해시 저장만 하고 게이트는 아직 없음.
- 배포: jarvis 재시작만(웹 변경 없음). origin push 직접.
