# 종료된 회의 열람 (기록+요약) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 종료 후에도 비번으로 중계방에 입장해 전체 기록 + 언어별 요약을 본다 — DO 는 데이터를 보관하지 않고 jarvis(SQLite)에 온디맨드로 요청·중계.

**Architecture:** viewer 가 종료된 mid 로 입장하면 DO 가 publish 소켓으로 `archive_request{req,mid,pw}` 를 jarvis 에 보내고, jarvis 가 SQLite 조회·비번검증 후 `archive_response{req,ok,…}` 회신 → DO 가 해당 소켓에만 `meeting_archive` 중계. 요약 완료 시 jarvis 가 `meeting_summary` 전송(보관 X). eviction 은 종료가 아니라 새 회의 시작 시점으로.

**Tech Stack:** Python 3.11 + pytest; TS Worker/DO(`npm run typecheck`); 정적 HTML 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-06-ended-meeting-archive-design.md`

**전제:** 회의 ID/비번/기록·다국어 머지됨. 와이어가 jarvis↔웹 묶임 → **둘 다 머지 후** jarvis 재시작 + 웹 배포.

---

## Task 1: relay_client — archive_request 인바운드 콜백

**Files:** Modify `relay_client.py`, `tests/test_relay_client.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_relay_client.py` 상단 import 에 `import json` 추가(없으면), 파일 끝에:

```python
def test_handle_inbound_archive_request_calls_callback():
    rc = _rc()
    got = []
    rc.on_archive_request = lambda m: got.append(m)
    rc._handle_inbound(json.dumps({"kind": "archive_request", "text": "{}"}))
    assert len(got) == 1 and got[0]["kind"] == "archive_request"


def test_handle_inbound_archive_request_no_callback_safe():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "archive_request", "text": "{}"}))  # on_archive_request=None → no crash


def test_handle_inbound_viewers_still_works():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "viewers", "count": 3}))
    assert rc.web_viewer_count == 3
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q`
Expected: FAIL (`on_archive_request` 속성 없음 → AttributeError)

- [ ] **Step 3: 구현** — `relay_client.py`

(a) `__init__` 의 `self.web_viewer_count = 0 ...` 줄 아래 추가:
```python
        self.on_archive_request = None   # DO → archive_request 콜백(설정 시 호출)
```
(b) `_handle_inbound` 의 `if m.get("kind") == "viewers":` 블록 아래에 추가:
```python
        elif m.get("kind") == "archive_request" and self.on_archive_request:
            try:
                self.on_archive_request(m)
            except Exception as e:
                self.on_log(f"[relay] archive_request 처리 오류: {e}")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**
```bash
git add relay_client.py tests/test_relay_client.py
git commit -m "feat(relay): archive_request 인바운드 콜백(on_archive_request)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: meeting_store — archive_response 페이로드 빌더

**Files:** Modify `meeting_store.py`, `tests/test_meeting_store.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_store.py` 끝에 (`import json` 은 이미 있음)

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: FAIL (archive_response 미정의)

- [ ] **Step 3: 구현** — `meeting_store.py`

상단 `import json` 아래에 `import hashlib` 추가. 파일 끝(클래스 밖, 모듈 함수)에 추가:
```python
def archive_response(row: dict | None, pw: str, req) -> dict:
    """저장 행(dict|None) + 평문 pw → archive_response 페이로드.
    pw 해시(sha256, hash_password 와 동일)가 row 의 password_hash 와 일치해야 ok."""
    pw_hash = hashlib.sha256((pw or "").encode()).hexdigest()
    if not row or pw_hash != (row.get("password_hash") or ""):
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
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**
```bash
git add meeting_store.py tests/test_meeting_store.py
git commit -m "feat(meeting): meeting_store.archive_response — 행+비번 → 열람 페이로드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: main 배선 — _serve_archive + meeting_summary emit

**Files:** Modify `main.py`. 검증: import + pytest.

- [ ] **Step 1: import 갱신** — `from meeting_store import MeetingStore` 를 교체:
```python
from meeting_store import MeetingStore, archive_response
```

- [ ] **Step 2: _serve_archive 정의 + 배선** — `_save_meeting` 정의 **위**(store 생성 다음)에 추가:
```python
    def _serve_archive(msg):
        """DO → archive_request: SQLite 조회·검증 후 archive_response 회신."""
        try:
            data = json.loads(msg.get("text") or "{}")
        except Exception:
            return
        payload = archive_response(store.get(data.get("mid") or ""),
                                   data.get("pw") or "", data.get("req"))
        if web_pub is not None:
            web_pub.emit("archive_response", json.dumps(payload, ensure_ascii=False))

    if web_pub is not None:
        web_pub.on_archive_request = _serve_archive
```

- [ ] **Step 3: _save_meeting 에 meeting_summary emit** — `_save_meeting` 의 `_run` 내부, `console.log(f"📝 회의 요약 저장됨 ...")` 줄 **아래**에 추가(같은 `if summaries:`→`try` 블록 안, set_summary 성공 경로):
```python
                    if web_pub is not None:
                        web_pub.emit("meeting_summary", json.dumps(
                            {"mid": record["id"], "summaries": summaries}, ensure_ascii=False))
```

- [ ] **Step 4: 검증**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 5: 커밋**
```bash
git add main.py
git commit -m "feat(meeting): main _serve_archive(온디맨드 회신) + 요약완료 meeting_summary

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: types.ts — 신규 kind + PUBLIC_KINDS

**Files:** Modify `jarvis-web/src/types.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: EventKind 추가** — `| "meeting_info" ...` 줄 아래에:
```typescript
  | "archive_request"      // DO → jarvis: 종료 회의 열람 요청. text=JSON{req,mid,pw}
  | "archive_response"     // jarvis → DO: 열람 응답. text=JSON{req,ok,title,transcript,summaries}
  | "meeting_archive"      // DO → viewer: 종료 회의 기록. text=JSON{title,transcript,summaries}
  | "meeting_summary"      // jarvis → viewer: 언어별 요약(준비되면). text=JSON{mid,summaries}
```

- [ ] **Step 2: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts
git commit -m "feat(web): archive_request/response·meeting_archive/summary EventKind

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: meeting_do.ts — 온디맨드 중계 + evict 이동

**Files:** Modify `jarvis-web/src/meeting_do.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: 필드 추가** — `private currentMeetingId: string | null = null;` 줄 위(또는 근처)에:
```typescript
  private pendingArchive: Map<number, WebSocket> = new Map();
  private archiveSeq = 0;
```

- [ ] **Step 2: PUBLIC_KINDS 에 추가** — `const PUBLIC_KINDS = new Set([...])` 의 배열에 `"meeting_archive", "meeting_summary"` 추가(예: `"publisher_disconnected",` 다음):
```typescript
  "meeting_archive", "meeting_summary",
```
(archive_request/response 는 미포함 — DO↔jarvis 전용.)

- [ ] **Step 3: end 케이스에서 evict 제거** — `end` 케이스의
```typescript
      this.broadcast(this.buildEvent(msg));
      this.evictPublicViewers();   // 회의 종료 — 공개 viewer 는 다음 회의 비번 재인증 필요
```
를(evict 줄 삭제):
```typescript
      this.broadcast(this.buildEvent(msg));
```

- [ ] **Step 4: navigate(home) 에서 evict 제거** — navigate 케이스의
```typescript
        this.lastMeetingInfo = null;
        this.evictPublicViewers();   // 회의 종료 — 공개 viewer 강제 해제(다음 회의 재인증)
      }
```
를:
```typescript
        this.lastMeetingInfo = null;
      }
```

- [ ] **Step 5: meeting_creds 에서 evict 추가** — `meeting_creds` 케이스를 교체(새 회의 신원 등장 → 이전 viewer 해제):
```typescript
    if (msg.kind === "meeting_creds") {
      this.evictPublicViewers();   // 새 회의 — 이전 회의 보던 viewer 해제(재인증 강제)
      try {
        const c = JSON.parse(msg.text || "{}");
        this.currentMeetingId = c.meeting_id ?? null;
        this.currentPasswordHash = c.password_hash ?? null;
      } catch { /* */ }
      return;
    }
```

- [ ] **Step 6: archive_response + meeting_summary 핸들 추가** — `meeting_info` 케이스 아래에:
```typescript
    if (msg.kind === "archive_response") {
      let d: any;
      try { d = JSON.parse(msg.text || "{}"); } catch { return; }
      const ws = this.pendingArchive.get(d.req);
      if (!ws) return;
      this.pendingArchive.delete(d.req);
      if (!d.ok) { try { ws.close(4003, "no-archive"); } catch { /* */ } return; }
      this.safeSend(ws, this.buildEvent({ kind: "meeting_archive",
        text: JSON.stringify({ title: d.title, transcript: d.transcript, summaries: d.summaries }) }));
      this.attachViewer(ws, "public");   // 이후 meeting_summary 수신
      return;
    }
    if (msg.kind === "meeting_summary") {
      this.broadcast(this.buildEvent(msg));
      return;
    }
```

- [ ] **Step 7: attachWatchPending 교체** — 라이브 매칭이면 라이브, 아니면 archive 요청:
```typescript
  private attachWatchPending(ws: WebSocket): void {
    let done = false;
    const timer = setTimeout(() => {
      if (!done) { try { ws.close(4003, "no-auth"); } catch { /* */ } }
    }, 10000);
    ws.addEventListener("message", async (evt) => {
      if (done) return;
      let msg: any;
      try { msg = JSON.parse(typeof evt.data === "string" ? evt.data : ""); } catch { return; }
      if (!msg || msg.kind !== "auth") return;
      // 라이브 회의 매칭 → 라이브 합류
      if (this.currentMeetingId && msg.mid === this.currentMeetingId) {
        const h = await sha256hex(String(msg.pw || ""));
        done = true; clearTimeout(timer);
        if (h === this.currentPasswordHash) { this.attachViewer(ws, "public"); }
        else { try { ws.close(4003, "bad-password"); } catch { /* */ } }
        return;
      }
      // 라이브가 아닌 mid → 종료/과거 회의 archive 요청(jarvis 에 중계)
      done = true; clearTimeout(timer);
      if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
      const req = ++this.archiveSeq;
      this.pendingArchive.set(req, ws);
      this.safeSend(this.publisher, this.buildEvent({
        kind: "archive_request",
        text: JSON.stringify({ req, mid: msg.mid, pw: msg.pw }),
      }));
      setTimeout(() => {
        if (this.pendingArchive.delete(req)) { try { ws.close(4003, "archive-timeout"); } catch { /* */ } }
      }, 10000);
    });
    ws.addEventListener("close", () => clearTimeout(timer));
  }
```

- [ ] **Step 8: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 9: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(web/DO): 종료 회의 온디맨드 중계(archive_request/response) + evict 를 새 회의 시작으로

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: viewer.html — 기록/요약 렌더

**Files:** Modify `jarvis-web/src/static/viewer.html`. 검증: JS 구문.

- [ ] **Step 1: 요약 패널 CSS** — `</style>` 직전에:
```css
  #summary { padding: 14px 16px; border-bottom: 2px solid var(--border); background: var(--bg); }
  #summary.hidden { display: none; }
  #summary h3 { font-size: 15px; margin: 0 0 8px; }
  #summary .sum { margin: 8px 0; }
  #summary .sum-lang { font-size: 13px; color: var(--partial); margin-bottom: 2px; }
  #summary .sum-body { white-space: pre-wrap; font-size: 14px; }
```

- [ ] **Step 2: 요약 패널 element** — `<main id="log"></main>` **위**에:
```html
  <div id="summary" class="hidden"></div>
```

- [ ] **Step 3: renderSummaries 헬퍼 + handle 케이스** — `<script>` 안 `function handle(ev) {` **위**에 헬퍼 추가:
```javascript
  function renderSummaries(summaries) {
    const el = $("summary");
    if (!summaries || !Object.keys(summaries).length) { el.classList.add("hidden"); return; }
    const names = { ko: "🇰🇷 한국어", en: "🇺🇸 English", ja: "🇯🇵 日本語", zh: "🇨🇳 中文" };
    let html = "<h3>📝 회의 요약</h3>";
    for (const [lg, txt] of Object.entries(summaries)) {
      html += `<div class="sum"><div class="sum-lang">${names[lg] || lg}</div><div class="sum-body">${escapeHtml(txt)}</div></div>`;
    }
    el.innerHTML = html;
    el.classList.remove("hidden");
  }
```
그리고 `handle()` 의 switch 에 케이스 추가(예: `case "end":` 위):
```javascript
      case "meeting_archive": {
        let a; try { a = JSON.parse(ev.text || "{}"); } catch { return; }
        if (a.title) $("title").textContent = a.title;
        if ($("log").childElementCount === 0 && Array.isArray(a.transcript)) {
          const fl = { ko: "🇰🇷", en: "🇺🇸", ja: "🇯🇵", zh: "🇨🇳" };
          for (const e of a.transcript) {
            const c = newCard();
            let html = `<div class="src">🧑 ${escapeHtml(e.source || "")}</div>`;
            for (const [lg, t] of Object.entries(e.translations || {})) {
              html += `<div class="tx ${lg}">${fl[lg] || "🌐"} ${escapeHtml(t)}</div>`;
            }
            c.innerHTML = html;
          }
        }
        renderSummaries(a.summaries);
        return;
      }
      case "meeting_summary": {
        let s; try { s = JSON.parse(ev.text || "{}"); } catch { return; }
        renderSummaries(s.summaries);
        return;
      }
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
```
Expected: `viewer JS OK`.

- [ ] **Step 5: 커밋**
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): 자막 페이지 종료 회의 기록(meeting_archive)+요약(meeting_summary) 렌더

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, relay_client, meeting_store; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(e.message);process.exit(1);}}console.log('viewer JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `viewer JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포 + jarvis 재시작 후)**
- 회의 진행 → /stop → 같은 링크 `/{room}/meeting/{mid}` 새 탭 → 비번 입장 → 전체 기록 표시, 잠시 후 요약 패널 채워짐.
- 라이브로 보던 탭: 종료 후 끊기지 않고 요약 패널 갱신.
- 틀린 비번/없는 mid → 게이트 거부(4003). jarvis 끄고 입장 시도 → 거부.
- 새 회의 시작 → 이전 종료-열람 탭은 4003 으로 끊겨 재인증 게이트.

---

## 비고
- DO 는 회의 데이터 무보관(pendingArchive 는 req→소켓 임시 라우팅). jarvis off 면 열람 불가(의도).
- 라이브 회의 비번 오류는 즉시 4003(라이브 mid 매칭 시 로컬 검증); 그 외 mid 는 archive 요청 → jarvis 가 SQLite 없으면 ok:false.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
