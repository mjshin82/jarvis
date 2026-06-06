# 중계 관리자 권한 (비번 생략 + 회의 목록) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자(ADMIN_PASSWORD)는 개별 회의 중계를 비번 없이 열람하고, `/{room}/meeting` 에서 최근 회의 20개 목록(관리자 전용)을 보고 클릭해 들어간다.

**Architecture:** 관리자 검증은 Worker(`/watch?token=`→requireAdmin)에서만 — 통과 시 DO 내부경로에 `admin=1`. DO 는 그 플래그를 신뢰해 비번 검증을 생략하고, 기록·목록은 jarvis SQLite 온디맨드(archive 패턴 답습, DO 무보관). 신규 `list.html` 가 목록 페이지.

**Tech Stack:** Python 3.11 + pytest; TS Worker/DO(`npm run typecheck`); 정적 HTML 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-06-admin-meeting-access-design.md`

**전제:** 종료-회의 열람(archive) 머지됨. jarvis+웹 와이어 묶임 → 둘 다 머지 후 재시작·배포.

---

## Task 1: meeting_store — recent() + archive_response(admin=)

**Files:** Modify `meeting_store.py`, `tests/test_meeting_store.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_store.py` 끝에

```python
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
    r = archive_response(store.get("m1"), "WRONG", 9, admin=True)   # 비번 틀려도 admin
    assert r["ok"] is True and r["title"] == "주간"
    assert archive_response(None, "x", 1, admin=True) == {"req": 1, "ok": False}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: FAIL (recent 없음 / admin 인자 없음)

- [ ] **Step 3: 구현** — `meeting_store.py`

(a) `MeetingStore` 에 메서드 추가(`get` 아래):
```python
    def recent(self, limit: int = 20) -> list:
        """최근 회의 메타 목록(시작 시각 내림차순). 목록 페이지용 — 본문/요약 제외."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, started_at, ended_at FROM meetings "
                "ORDER BY started_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
```
(b) `archive_response` 시그니처/검증을 admin 지원으로 교체:
```python
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
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add meeting_store.py tests/test_meeting_store.py
git commit -m "feat(meeting): meeting_store.recent(목록) + archive_response admin 비번생략

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: relay_client — list_request 인바운드 콜백

**Files:** Modify `relay_client.py`, `tests/test_relay_client.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_relay_client.py` 끝에
```python
def test_handle_inbound_list_request_calls_callback():
    rc = _rc()
    got = []
    rc.on_list_request = lambda m: got.append(m)
    rc._handle_inbound(json.dumps({"kind": "list_request", "text": "{}"}))
    assert len(got) == 1 and got[0]["kind"] == "list_request"


def test_handle_inbound_list_request_no_callback_safe():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "list_request", "text": "{}"}))  # on_list_request=None → no crash
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q`
Expected: FAIL (on_list_request 없음)

- [ ] **Step 3: 구현** — `relay_client.py`

(a) `__init__` 의 `self.on_archive_request = None` 줄 아래에:
```python
        self.on_list_request = None      # DO → list_request 콜백(설정 시 호출)
```
(b) `_handle_inbound` 의 `archive_request` elif 아래에:
```python
        elif m.get("kind") == "list_request" and self.on_list_request:
            try:
                self.on_list_request(m)
            except Exception as e:
                self.on_log(f"[relay] list_request 처리 오류: {e}")
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add relay_client.py tests/test_relay_client.py
git commit -m "feat(relay): list_request 인바운드 콜백(on_list_request)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: main — _serve_list 배선 + _serve_archive admin 분기

**Files:** Modify `main.py`. 검증: import + pytest.

- [ ] **Step 1: _serve_archive 에 admin 전달** — `_serve_archive` 의 `payload = archive_response(...)` 호출을 교체:
```python
        payload = archive_response(store.get(data.get("mid") or ""),
                                   data.get("pw") or "", data.get("req"),
                                   admin=bool(data.get("admin")))
```

- [ ] **Step 2: _serve_list 정의 + 배선** — `if web_pub is not None: web_pub.on_archive_request = _serve_archive` 근처(같은 블록)에 추가. `_serve_archive` 정의 아래에:
```python
    def _serve_list(msg):
        """DO → list_request: 최근 회의 20개 회신(관리자 전용 — DO 가 admin 검증)."""
        try:
            data = json.loads(msg.get("text") or "{}")
        except Exception:
            return
        meetings = store.recent(20)
        if web_pub is not None:
            web_pub.emit("list_response", json.dumps(
                {"req": data.get("req"), "ok": True, "meetings": meetings}, ensure_ascii=False))

    if web_pub is not None:
        web_pub.on_list_request = _serve_list
```
(`web_pub.on_archive_request = _serve_archive` 배선은 그대로 두고 그 옆/아래에 on_list_request 추가.)

- [ ] **Step 3: 검증**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 4: 커밋**
```bash
git add main.py
git commit -m "feat(meeting): main _serve_list(목록 회신) + _serve_archive admin 분기

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: types.ts — list / list_request / list_response / meeting_list

**Files:** Modify `jarvis-web/src/types.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: EventKind 추가** — `| "meeting_summary" ...` 줄 아래에:
```typescript
  | "list"                 // viewer(admin) → DO: 최근 회의 목록 요청
  | "list_request"         // DO → jarvis: 목록 요청. text=JSON{req}
  | "list_response"        // jarvis → DO: 목록 응답. text=JSON{req,ok,meetings}
  | "meeting_list"         // DO → viewer: 목록. text=JSON{meetings}
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
git commit -m "feat(web): list/list_request/list_response/meeting_list EventKind

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: meeting_do.ts — isAdmin watch · 비번생략 · 목록 중계

**Files:** Modify `jarvis-web/src/meeting_do.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: watch 라우팅에 admin 플래그** — `fetch` 의
```typescript
    } else if (role === "watch") {
      this.attachWatchPending(server);
    } else if (role === "mic") {
```
를:
```typescript
    } else if (role === "watch") {
      this.attachWatchPending(server, url.searchParams.get("admin") === "1");
    } else if (role === "mic") {
```

- [ ] **Step 2: list_response 핸들 추가** — `handlePublisherMessage` 의 `archive_response` 케이스 아래에:
```typescript
    if (msg.kind === "list_response") {
      let d: any;
      try { d = JSON.parse(msg.text || "{}"); } catch { return; }
      const ws = this.pendingArchive.get(d.req);
      if (!ws) return;
      this.pendingArchive.delete(d.req);
      this.safeSend(ws, this.buildEvent({ kind: "meeting_list", text: JSON.stringify({ meetings: d.meetings || [] }) }));
      return;
    }
```

- [ ] **Step 3: attachWatchPending 전체 교체** — isAdmin 인자 + list/auth 처리:
```typescript
  private attachWatchPending(ws: WebSocket, isAdmin: boolean): void {
    let done = false;
    const timer = setTimeout(() => {
      if (!done) { try { ws.close(4003, "no-auth"); } catch { /* */ } }
    }, 10000);
    ws.addEventListener("message", async (evt) => {
      if (done) return;
      let msg: any;
      try { msg = JSON.parse(typeof evt.data === "string" ? evt.data : ""); } catch { return; }
      if (!msg) return;
      // 관리자 목록 요청
      if (msg.kind === "list") {
        done = true; clearTimeout(timer);
        if (!isAdmin) { try { ws.close(4003, "admin-only"); } catch { /* */ } return; }
        if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
        const req = ++this.archiveSeq;
        this.pendingArchive.set(req, ws);
        this.safeSend(this.publisher, this.buildEvent({ kind: "list_request", text: JSON.stringify({ req }) }));
        setTimeout(() => { if (this.pendingArchive.delete(req)) { try { ws.close(4003, "list-timeout"); } catch { /* */ } } }, 10000);
        return;
      }
      if (msg.kind !== "auth") return;
      done = true; clearTimeout(timer);
      const live = this.currentMeetingId && msg.mid === this.currentMeetingId;
      // 관리자: 비번 생략
      if (isAdmin) {
        if (live) { this.attachViewer(ws, "public"); return; }
        if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
        const req = ++this.archiveSeq;
        this.pendingArchive.set(req, ws);
        this.safeSend(this.publisher, this.buildEvent({ kind: "archive_request", text: JSON.stringify({ req, mid: msg.mid, admin: true }) }));
        setTimeout(() => { if (this.pendingArchive.delete(req)) { try { ws.close(4003, "archive-timeout"); } catch { /* */ } } }, 10000);
        return;
      }
      // 비관리자: 라이브 비번검증 / 종료 archive
      if (live) {
        const h = await sha256hex(String(msg.pw || ""));
        if (h === this.currentPasswordHash) { this.attachViewer(ws, "public"); }
        else { try { ws.close(4003, "bad-password"); } catch { /* */ } }
        return;
      }
      if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
      const req = ++this.archiveSeq;
      this.pendingArchive.set(req, ws);
      this.safeSend(this.publisher, this.buildEvent({ kind: "archive_request", text: JSON.stringify({ req, mid: msg.mid, pw: msg.pw }) }));
      setTimeout(() => { if (this.pendingArchive.delete(req)) { try { ws.close(4003, "archive-timeout"); } catch { /* */ } } }, 10000);
    });
    ws.addEventListener("close", () => clearTimeout(timer));
  }
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(web/DO): watch isAdmin — 비번생략 + 관리자 목록 중계(list_request/response)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: index.ts — /watch admin 토큰 → DO admin 플래그

**Files:** Modify `jarvis-web/src/index.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: /watch 가 admin 전달** — `/watch/:key` 핸들러를 교체:
```typescript
app.get("/watch/:key", async (c) => {
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "watch", c.req.raw, requireAdmin(c));
});
```

- [ ] **Step 2: forwardToDO 에 admin 파라미터** — 시그니처/본문 교체:
```typescript
function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv" | "control" | "control-recv" | "watch", original: Request, admin = false): Promise<Response> {
  const id = env.MEETING_DO.idFromName(key);
  const stub = env.MEETING_DO.get(id);
  const internalUrl = new URL(original.url);
  internalUrl.pathname = `/__do/${role}/${encodeURIComponent(key)}`;
  if (admin) internalUrl.searchParams.set("admin", "1");
  const req = new Request(internalUrl.toString(), original);
  return stub.fetch(req);
}
```

- [ ] **Step 3: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 4: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/index.ts
git commit -m "feat(web): /watch 관리자 토큰 검증 → DO admin=1 전달

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: viewer.html — 관리자면 비번 게이트 생략

**Files:** Modify `jarvis-web/src/static/viewer.html`. 검증: JS 구문.

- [ ] **Step 1: adminPw 읽기** — `<script>` IIFE 안 `const mid = ...` 줄 아래에:
```javascript
  const adminPw = localStorage.getItem("jarvis_admin_pw") || "";
```

- [ ] **Step 2: connect 가 admin 토큰 사용** — `connect()` 의 `ws = new WebSocket(...)` 줄을 교체:
```javascript
    const base = `${proto}//${location.host}/watch/${encodeURIComponent(key)}`;
    ws = new WebSocket(adminPw ? `${base}?token=${encodeURIComponent(adminPw)}` : base);
```

- [ ] **Step 3: 관리자 자동 연결(게이트 생략)** — `<script>` 맨 끝(`$("gate-pw").addEventListener("keydown", ...)` 줄 아래, IIFE 닫기 전)에:
```javascript
  if (adminPw) { $("gate").classList.add("hidden"); connect(); }
```
(adminPw 있으면 게이트 숨기고 즉시 연결; 토큰 무효 시 DO 가 pw 게이트로 폴백 → close 4003 → 기존 핸들러가 게이트 다시 표시.)

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
```
Expected: `viewer JS OK`.

- [ ] **Step 5: 커밋**
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): 관리자(localStorage 비번) 시 회의 비번 게이트 생략

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: list.html (신규) + /{room}/meeting 라우트

**Files:** Create `jarvis-web/src/static/list.html`; Modify `jarvis-web/src/index.ts`. 검증: JS 구문 + typecheck.

- [ ] **Step 1: list.html 작성** — `jarvis-web/src/static/list.html`:
```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>회의 목록</title>
<style>
  :root { --bg:#fff; --fg:#111418; --muted:#6b7280; --card:#f5f6f8; --border:#e5e7eb; --accent:#2563eb; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0e1116; --fg:#e8eaed; --muted:#9aa0a6; --card:#161a21; --border:#2a2f37; --accent:#60a5fa; } }
  html, body { margin:0; padding:0; background:var(--bg); color:var(--fg); font-family:-apple-system,system-ui,sans-serif; }
  header { padding:14px 16px; border-bottom:1px solid var(--border); font-weight:600; }
  main { max-width:980px; margin:0 auto; width:100%; box-sizing:border-box; padding:16px; }
  .row { display:block; width:100%; text-align:left; background:var(--card); border:1px solid var(--border);
    border-radius:12px; padding:12px 14px; margin:10px 0; cursor:pointer; color:var(--fg); font:inherit; }
  .row .t { font-size:16px; font-weight:600; }
  .row .d { font-size:13px; color:var(--muted); margin-top:4px; }
  #msg { color:var(--muted); padding:8px 0; }
  #gate { position:fixed; inset:0; background:var(--bg); display:flex; align-items:center; justify-content:center; }
  #gate.hidden { display:none; }
  .gate-box { display:flex; flex-direction:column; gap:10px; width:min(86vw,300px); }
  #gate input { font-size:16px; padding:10px; border:1px solid #888; border-radius:8px; }
  #gate button { font-size:15px; padding:10px; border-radius:8px; cursor:pointer; }
</style>
</head>
<body>
  <div id="gate" class="hidden">
    <div class="gate-box">
      <div style="font-weight:600">🔒 관리자 로그인</div>
      <input id="gate-pw" type="password" placeholder="관리자 비번" />
      <button id="gate-go">입장</button>
    </div>
  </div>
  <header>최근 회의</header>
  <main><div id="msg">불러오는 중…</div><div id="list"></div></main>
<script>
(() => {
  const $ = (id) => document.getElementById(id);
  const room = decodeURIComponent(location.pathname.split("/").filter(Boolean)[0] || "jarvis");
  const KEY = "jarvis_admin_pw";
  function fmt(s) { try { return new Date(s).toLocaleString(); } catch { return s || ""; } }
  function render(meetings) {
    const wrap = $("list");
    wrap.innerHTML = "";
    if (!meetings || !meetings.length) { $("msg").textContent = "저장된 회의 없음"; return; }
    $("msg").textContent = "";
    for (const m of meetings) {
      const b = document.createElement("button");
      b.className = "row";
      b.innerHTML = `<div class="t"></div><div class="d"></div>`;
      b.querySelector(".t").textContent = m.title || "회의";
      b.querySelector(".d").textContent = fmt(m.started_at);
      b.addEventListener("click", () => { location.href = `/${encodeURIComponent(room)}/meeting/${encodeURIComponent(m.id)}`; });
      wrap.appendChild(b);
    }
  }
  function connect(pw) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/watch/${encodeURIComponent(room)}?token=${encodeURIComponent(pw)}`);
    ws.addEventListener("open", () => ws.send(JSON.stringify({ kind: "list" })));
    ws.addEventListener("message", (e) => {
      try { const ev = JSON.parse(e.data); if (ev.kind === "meeting_list") render(JSON.parse(ev.text || "{}").meetings); } catch {}
    });
    ws.addEventListener("close", (e) => {
      if (e.code === 4003) {
        localStorage.removeItem(KEY);
        $("msg").textContent = "관리자 전용입니다.";
        $("gate").classList.remove("hidden");
      }
    });
  }
  $("gate-go").addEventListener("click", () => {
    const pw = $("gate-pw").value.trim();
    if (!pw) return;
    localStorage.setItem(KEY, pw);
    $("gate").classList.add("hidden");
    $("msg").textContent = "불러오는 중…";
    connect(pw);
  });
  $("gate-pw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("gate-go").click(); });
  const saved = localStorage.getItem(KEY) || "";
  if (saved) connect(saved); else { $("msg").textContent = ""; $("gate").classList.remove("hidden"); }
})();
</script>
</body>
</html>
```

- [ ] **Step 2: index.ts — LIST_HTML import + 라우트**

(a) import 추가(`import VIEWER_HTML ...` 아래):
```typescript
import LIST_HTML from "./static/list.html";
```
(b) `/:name/meeting` 라우트를 LIST_HTML 로 교체:
```typescript
app.get("/:name/meeting", (c) => {
  return new Response(LIST_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```
(`/:name/meeting/:mid` 는 VIEWER_HTML 유지.)

- [ ] **Step 3: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/list.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('list JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `list JS OK`, typecheck 0.

- [ ] **Step 4: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/list.html jarvis-web/src/index.ts
git commit -m "feat(web): /{room}/meeting 관리자 회의 목록 페이지(list.html)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, relay_client, meeting_store; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');for(const f of ['jarvis-web/src/static/viewer.html','jarvis-web/src/static/list.html']){const h=fs.readFileSync(f,'utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(f,e.message);process.exit(1);}}}console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포·재시작 후)**
- 관리자(owner 앱 로그인한 같은 브라우저): `/{room}/meeting` → 최근 회의 목록(최신순), 클릭 → 비번 없이 열람.
- 관리자: 종료 회의 직접 링크(`/{mid}`) → 비번 없이 기록+요약.
- 비관리자(시크릿창): `/{mid}` → 회의 비번 게이트. `/{room}/meeting` → "관리자 전용".
- 잘못된 관리자 비번 → 목록 페이지 게이트 / viewer 는 비번 게이트 폴백.

---

## 비고
- DO 무보관 유지 — 목록·기록 모두 jarvis SQLite 온디맨드, 관리자 검증은 Worker.
- list_request/list_response 도 archive 와 같은 `pendingArchive`(req→소켓) 라우팅 재사용.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
