# 회의 목록 항목 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 회의 목록(`/{room}/meeting`)에서 🗑+confirm 으로 특정 회의를 meetings.db 에서 삭제한다.

**Architecture:** 기존 list/archive 온디맨드 중계 답습 — list.html 이 목록 소켓으로 `{kind:"delete",id}` → DO(관리자 검증, 영구 명령 채널) → `delete_request` → jarvis `meeting_store.delete` → `delete_response` → `meeting_deleted` → 행 DOM 제거. DO 무보관.

**Tech Stack:** Python 3.11 + pytest; TS Worker/DO(`npm run typecheck`); 정적 HTML 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-07-meeting-delete-design.md`

**전제:** 관리자 목록(admin-meeting-access) 머지됨. jarvis+웹 묶임 → 둘 다 머지 후 재시작·배포.

---

## Task 1: meeting_store.delete(id)

**Files:** Modify `meeting_store.py`, `tests/test_meeting_store.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_store.py` 끝에
```python
def test_delete_removes_only_target(tmp_path):
    from meeting_store import MeetingStore
    store = MeetingStore(str(tmp_path / "m.db"))
    for i in ("a", "b"):
        store.save({"id": i, "password_hash": "h", "title": i,
                    "started_at": "s", "ended_at": "e", "transcript": []})
    store.delete("a")
    assert store.get("a") is None
    assert store.get("b") is not None
    store.delete("nope")    # 없는 id 삭제 — 예외 없이 무시
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: FAIL (delete 없음)

- [ ] **Step 3: 구현** — `MeetingStore` 에 메서드 추가(`recent` 아래):
```python
    def delete(self, meeting_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add meeting_store.py tests/test_meeting_store.py
git commit -m "feat(meeting): meeting_store.delete(id)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: relay_client — delete_request 인바운드 콜백

**Files:** Modify `relay_client.py`, `tests/test_relay_client.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_relay_client.py` 끝에
```python
def test_handle_inbound_delete_request_calls_callback():
    rc = _rc()
    got = []
    rc.on_delete_request = lambda m: got.append(m)
    rc._handle_inbound(json.dumps({"kind": "delete_request", "text": "{}"}))
    assert len(got) == 1 and got[0]["kind"] == "delete_request"


def test_handle_inbound_delete_request_no_callback_safe():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "delete_request", "text": "{}"}))  # on_delete_request=None → no crash
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q`
Expected: FAIL (on_delete_request 없음)

- [ ] **Step 3: 구현** — `relay_client.py`
(a) `__init__` 의 `self.on_list_request = None` 줄 아래:
```python
        self.on_delete_request = None    # DO → delete_request 콜백(설정 시 호출)
```
(b) `_handle_inbound` 의 `list_request` elif 아래:
```python
        elif m.get("kind") == "delete_request" and self.on_delete_request:
            try:
                self.on_delete_request(m)
            except Exception as e:
                self.on_log(f"[relay] delete_request 처리 오류: {e}")
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add relay_client.py tests/test_relay_client.py
git commit -m "feat(relay): delete_request 인바운드 콜백(on_delete_request)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: main — _serve_delete 배선

**Files:** Modify `main.py`. 검증: import + pytest.

- [ ] **Step 1: _serve_delete 정의 + 배선** — `_serve_list` 정의 + `web_pub.on_list_request = _serve_list` 블록 아래에 추가:
```python
    def _serve_delete(msg):
        """DO → delete_request: 회의 삭제(관리자 전용 — DO 가 admin 검증)."""
        try:
            data = json.loads(msg.get("text") or "{}")
        except Exception:
            return
        mid = data.get("id") or ""
        try:
            store.delete(mid)
            ok = True
        except Exception as e:
            console.log(f"회의 삭제 실패({mid}): {e}")
            ok = False
        if web_pub is not None:
            web_pub.emit("delete_response", json.dumps(
                {"req": data.get("req"), "ok": ok, "id": mid}, ensure_ascii=False))

    if web_pub is not None:
        web_pub.on_delete_request = _serve_delete
```

- [ ] **Step 2: 검증**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 3: 커밋**
```bash
git add main.py
git commit -m "feat(meeting): main _serve_delete(회의 삭제 회신) 배선

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: types.ts — delete 관련 kind

**Files:** Modify `jarvis-web/src/types.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: EventKind 추가** — `| "meeting_list" ...` 줄 아래:
```typescript
  | "delete"               // viewer(admin) → DO: 회의 삭제 요청. {id}
  | "delete_request"       // DO → jarvis: 삭제 요청. text=JSON{req,id}
  | "delete_response"      // jarvis → DO: 삭제 응답. text=JSON{req,ok,id}
  | "meeting_deleted"      // DO → viewer: 삭제 완료. text=JSON{id,ok}
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
git commit -m "feat(web): delete/delete_request/delete_response/meeting_deleted EventKind

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: meeting_do.ts — list/delete 영구 채널 + delete_response

**Files:** Modify `jarvis-web/src/meeting_do.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: list 분기 → list/delete 공통 분기로 교체** — `attachWatchPending` 의 현재 `list` 분기:
```typescript
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
```
를 (done 미설정 → 반복 명령 가능; 타임아웃 시 소켓 안 닫음):
```typescript
      // 관리자 명령 채널(list/delete) — 같은 소켓으로 반복 처리
      if (msg.kind === "list" || msg.kind === "delete") {
        clearTimeout(timer);
        if (!isAdmin) { try { ws.close(4003, "admin-only"); } catch { /* */ } return; }
        if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
        const req = ++this.archiveSeq;
        this.pendingArchive.set(req, ws);
        if (msg.kind === "list") {
          this.safeSend(this.publisher, this.buildEvent({ kind: "list_request", text: JSON.stringify({ req }) }));
        } else {
          this.safeSend(this.publisher, this.buildEvent({ kind: "delete_request", text: JSON.stringify({ req, id: msg.id }) }));
        }
        setTimeout(() => { this.pendingArchive.delete(req); }, 10000);   // 응답 타임아웃 — 소켓 유지
        return;
      }
```

- [ ] **Step 2: delete_response 핸들 추가** — `handlePublisherMessage` 의 `list_response` 케이스 아래:
```typescript
    if (msg.kind === "delete_response") {
      let d: any;
      try { d = JSON.parse(msg.text || "{}"); } catch { return; }
      const ws = this.pendingArchive.get(d.req);
      if (!ws) return;
      this.pendingArchive.delete(d.req);
      this.safeSend(ws, this.buildEvent({ kind: "meeting_deleted", text: JSON.stringify({ id: d.id, ok: d.ok }) }));
      return;
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
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(web/DO): 목록 소켓 영구 관리자 채널(list/delete) + delete_response

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: list.html — 🗑 버튼 + 행 제거

**Files:** Modify `jarvis-web/src/static/list.html`. 검증: JS 구문.

- [ ] **Step 1: CSS 교체** — 현재 `.row { ... }` 한 줄(`display:block; ... font:inherit;`)을 교체(`.row .t`/`.row .d` 규칙은 그대로 둠):
```css
  .row { display:flex; align-items:center; justify-content:space-between; gap:10px;
    background:var(--card); border:1px solid var(--border); border-radius:12px; padding:12px 14px; margin:10px 0; }
  .row-main { flex:1; min-width:0; cursor:pointer; }
  .row-del { background:transparent; border:none; cursor:pointer; font-size:18px; padding:4px 8px; color:var(--muted); }
```

- [ ] **Step 2: ws 를 모듈 스코프로** — `<script>` IIFE 안 `const KEY = ...` 줄 아래에:
```javascript
  let ws = null;
```

- [ ] **Step 3: render 교체** — 현재 `function render(meetings) { ... }` 전체를:
```javascript
  function render(meetings) {
    const wrap = $("list");
    wrap.innerHTML = "";
    if (!meetings || !meetings.length) { $("msg").textContent = "저장된 회의 없음"; return; }
    $("msg").textContent = "";
    for (const m of meetings) {
      const row = document.createElement("div");
      row.className = "row";
      row.dataset.id = m.id;
      const main = document.createElement("div");
      main.className = "row-main";
      main.innerHTML = `<div class="t"></div><div class="d"></div>`;
      main.querySelector(".t").textContent = m.title || "회의";
      main.querySelector(".d").textContent = fmt(m.started_at);
      main.addEventListener("click", () => { location.href = `/${encodeURIComponent(room)}/meeting/${encodeURIComponent(m.id)}`; });
      const del = document.createElement("button");
      del.className = "row-del"; del.textContent = "🗑"; del.title = "삭제";
      del.addEventListener("click", () => {
        if (!confirm("이 회의를 삭제할까요?")) return;
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ kind: "delete", id: m.id }));
      });
      row.appendChild(main); row.appendChild(del);
      wrap.appendChild(row);
    }
  }
```

- [ ] **Step 4: connect 교체(ws 할당 + meeting_deleted 처리)** — 현재 `function connect(pw) { ... }` 전체를:
```javascript
  function connect(pw) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/watch/${encodeURIComponent(room)}?token=${encodeURIComponent(pw)}`);
    ws.addEventListener("open", () => ws.send(JSON.stringify({ kind: "list" })));
    ws.addEventListener("message", (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.kind === "meeting_list") { render(JSON.parse(ev.text || "{}").meetings); }
        else if (ev.kind === "meeting_deleted") {
          const d = JSON.parse(ev.text || "{}");
          if (d.ok) { const r = $("list").querySelector(`.row[data-id="${CSS.escape(d.id)}"]`); if (r) r.remove(); }
        }
      } catch {}
    });
    ws.addEventListener("close", (e) => {
      if (e.code === 4003) {
        localStorage.removeItem(KEY);
        $("msg").textContent = "관리자 전용입니다.";
        $("gate").classList.remove("hidden");
      }
    });
  }
```

- [ ] **Step 5: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/list.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('list JS OK');"
```
Expected: `list JS OK`.

- [ ] **Step 6: 커밋**
```bash
git add jarvis-web/src/static/list.html
git commit -m "feat(web): 회의 목록 🗑 삭제 버튼 + confirm + 행 제거

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, relay_client, meeting_store; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/list.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(e.message);process.exit(1);}}console.log('list JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `list JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포·재시작 후)**
- 관리자: `/{room}/meeting` 목록 → 항목 🗑 클릭 → confirm → Yes → 행 사라짐 → 새로고침해도 없음(DB 삭제됨).
- confirm 취소 → 아무 일 없음.
- 비관리자: 목록 자체 차단(기존).

---

## 비고
- delete_request/response·meeting_deleted 모두 PUBLIC_KINDS 밖(관리자/내부 전용).
- 목록 소켓이 영구 명령 채널(list 후 delete 반복) — 응답 타임아웃 시 소켓 닫지 않음.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
