# 회의 비번 게이트 (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 비번(폼/콘솔 입력, 빈 값=자동)을 받아 DO 가 해시로 보관하고, 공개 자막 페이지(`/{room}/meeting/{id}`)를 첫-메시지 인증으로 게이팅하며, owner 에게 공유 링크+비번을 표시한다.

**Architecture:** jarvis 가 회의 시작 시 `meeting_creds{meeting_id,password_hash}`(DO 전용)와 `meeting_info{meeting_id,password}`(owner 표시)를 publisher 로 전송. DO 는 creds 를 보관하고, `watch` 소켓을 즉시 붙이지 않고 첫 메시지 `{kind:"auth",mid,pw}` 의 sha256 을 검증해 통과해야 public viewer 로 합류시킨다. 회의 종료 시 creds 초기화.

**Tech Stack:** Python 3.11 + pytest; TS Cloudflare Worker/DO(`npm run typecheck`, Web Crypto `crypto.subtle`); 정적 HTML 인라인 JS(`node --check`). DO 게이트는 수동 검증(JS 테스트 하니스 없음).

**스펙:** `docs/superpowers/specs/2026-06-06-meeting-id-password-record-design.md` (Phase 2)

**전제:** Phase 1 머지됨 — `MeetingMeta.meeting_id/password/started_at`, `hash_password()` 존재. `meeting_title` 이벤트/owner-replay 패턴 존재.

---

## Task 1: 비번 입력 — 콘솔 단계 + 웹 meeting_start 파싱 (live_translate.py + main.py)

**Files:** Modify `live_translate.py`, `main.py`, `tests/test_meeting_session.py`

- [ ] **Step 1: 테스트 — 기존 갱신 + 신규** — `tests/test_meeting_session.py`

(a) 기존 `test_setup_submit_title_and_vocab` 를 3단계로 갱신(2단계 submit 후 not done → password submit):
```python
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
```
(b) 파일 끝에 추가:
```python
def test_setup_password_empty_stays_blank():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit(""); s.submit(""); s.submit("")
    assert s.done
    assert s.meta.password == ""   # 빈 입력 → 세션 시작 시 자동 생성
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: FAIL (password 단계 없어 2 submit 후 done)

- [ ] **Step 3: live_translate.py 구현**

(a) `_META_STEPS` 에 password 단계 추가:
```python
_META_STEPS = (
    ("title", "회의 제목을 입력하세요 (Enter=기본)"),
    ("vocabulary", "워드북 — 쉼표로 구분 (Enter=기본: Jarvis, 이름)"),
    ("password", "비번 (Enter=자동 생성)"),
)
```
(b) `MeetingSetup.submit` 에 password 분기 추가(`elif key == "vocabulary":` 블록 다음):
```python
        elif key == "password":
            self.meta.password = v    # 빈 값이면 세션 시작 시 자동 생성
```

- [ ] **Step 4: main.py — meeting_start 비번 파싱**

`_on_remote_command` 의 `elif kind == "meeting_start":` 블록을 교체:
```python
            elif kind == "meeting_start":
                from live_translate import MeetingMeta
                title = (msg.get("title") or "").strip() or "회의"
                vocab = [v.strip() for v in (msg.get("vocabulary") or [])
                         if isinstance(v, str) and v.strip()]
                if not vocab:
                    vocab = ["Jarvis", config.USER_NAME]
                password = (msg.get("password") or "").strip()
                await controller.start_meeting(meta=MeetingMeta(
                    my_name=config.USER_NAME, title=title, vocabulary=vocab, password=password))
```

- [ ] **Step 5: 통과 + import + 전체**

Run:
```bash
.venv/bin/python -m pytest tests/test_meeting_session.py -q
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: PASS, `import ok`, 전체 통과.

- [ ] **Step 6: 커밋**
```bash
git add live_translate.py main.py tests/test_meeting_session.py
git commit -m "feat(meeting): 비번 입력 — 콘솔 /meet 3단계 + 웹 meeting_start password

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 회의 시작 시 creds/info 발행 + 비번 로그 (main.py)

**Files:** Modify `main.py`. 검증: import + pytest(웹 미관여).

- [ ] **Step 1: import 추가** — `from meeting_store import MeetingStore` 줄 아래:
```python
from live_translate import hash_password
```

- [ ] **Step 2: _after_meeting_start 확장**

`_after_meeting_start` 의 `web_pub.emit("meeting_title", sess.meta.title)` 줄 **아래**에 추가:
```python
            web_pub.emit("meeting_creds", json.dumps({
                "meeting_id": sess.meta.meeting_id,
                "password_hash": hash_password(sess.meta.password),
            }))
            web_pub.emit("meeting_info", json.dumps({
                "meeting_id": sess.meta.meeting_id,
                "password": sess.meta.password,
            }))
            console.log(f"🔒 비번: {sess.meta.password}")
```
(`json` 은 main.py 상단에 이미 import 됨.)

- [ ] **Step 3: 검증**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 4: 커밋**
```bash
git add main.py
git commit -m "feat(meeting): 회의 시작 시 meeting_creds/meeting_info 발행 + 비번 로그

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: types.ts — meeting_creds / meeting_info kind

**Files:** Modify `jarvis-web/src/types.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: EventKind 에 추가** — `| "meeting_title"        // jarvis → owner: 회의 제목(헤더 표시)` 줄 아래:
```typescript
  | "meeting_creds"        // jarvis → DO: 현재 회의 인증(미broadcast). text=JSON{meeting_id,password_hash}
  | "meeting_info"         // jarvis → owner: 공유용 링크/비번. text=JSON{meeting_id,password}
```
(둘 다 `PUBLIC_KINDS`(meeting_do.ts)에 **넣지 않는다** → 공개 viewer 미수신.)

- [ ] **Step 2: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts
git commit -m "feat(web): meeting_creds/meeting_info EventKind 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: meeting_do.ts — creds 보관 + watch 첫-메시지 인증 게이트 + info 동기화

**Files:** Modify `jarvis-web/src/meeting_do.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: sha256 헬퍼 추가** — 파일 상단 `const PUBLIC_KINDS = ...` 정의 **아래**:
```typescript
async function sha256hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
```

- [ ] **Step 2: 필드 추가** — `private lastMeetingTitle: string | null = null;` 줄 아래:
```typescript
  private currentMeetingId: string | null = null;
  private currentPasswordHash: string | null = null;
  private lastMeetingInfo: string | null = null;
```

- [ ] **Step 3: watch 라우팅 교체** — `fetch()` 의
```typescript
    } else if (role === "watch") {
      this.attachViewer(server, "public");
    } else if (role === "mic") {
```
를
```typescript
    } else if (role === "watch") {
      this.attachWatchPending(server);
    } else if (role === "mic") {
```
로 교체.

- [ ] **Step 4: attachWatchPending 메서드 추가** — `attachViewer` 메서드 **위**에:
```typescript
  // 공개 자막 소켓: 즉시 붙이지 않고 첫 메시지 {kind:"auth",mid,pw} 검증 후 합류.
  private attachWatchPending(ws: WebSocket): void {
    let authed = false;
    const timer = setTimeout(() => {
      if (!authed) { try { ws.close(4003, "no-auth"); } catch { /* */ } }
    }, 10000);
    ws.addEventListener("message", async (evt) => {
      if (authed) return;
      let msg: any;
      try { msg = JSON.parse(typeof evt.data === "string" ? evt.data : ""); } catch { return; }
      if (!msg || msg.kind !== "auth") return;
      if (!this.currentMeetingId) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
      if (msg.mid !== this.currentMeetingId) { try { ws.close(4003, "bad-meeting"); } catch { /* */ } return; }
      const h = await sha256hex(String(msg.pw || ""));
      if (h !== this.currentPasswordHash) { try { ws.close(4003, "bad-password"); } catch { /* */ } return; }
      authed = true;
      clearTimeout(timer);
      this.attachViewer(ws, "public");
    });
    ws.addEventListener("close", () => clearTimeout(timer));
  }
```

- [ ] **Step 5: handlePublisherMessage — creds/info 케이스 + 종료 정리**

(a) `meeting_title` 케이스(`if (msg.kind === "meeting_title") { ... }`) **아래**에 추가:
```typescript
    if (msg.kind === "meeting_creds") {
      try {
        const c = JSON.parse(msg.text || "{}");
        this.currentMeetingId = c.meeting_id ?? null;
        this.currentPasswordHash = c.password_hash ?? null;
      } catch { /* */ }
      return;   // DO 전용 — broadcast/append 안 함
    }
    if (msg.kind === "meeting_info") {
      this.lastMeetingInfo = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));   // 공개는 PUBLIC_KINDS 필터로 차단 → owner 만
      return;
    }
```
(b) `navigate` 케이스의 `if (msg.text !== "meeting") this.lastMeetingTitle = null;` 를 교체:
```typescript
      if (msg.text !== "meeting") {
        this.lastMeetingTitle = null;
        this.currentMeetingId = null;
        this.currentPasswordHash = null;
        this.lastMeetingInfo = null;
      }
```
(c) `end` 케이스의 첫 줄(`this.broadcast(this.buildEvent(msg));`) **위**에 정리 추가:
```typescript
      this.currentMeetingId = null;
      this.currentPasswordHash = null;
      this.lastMeetingInfo = null;
      this.lastMeetingTitle = null;
```

- [ ] **Step 6: attachViewer(owner) 재접속에 meeting_info 재전송** — `attachViewer` 의
```typescript
      if (this.lastMeetingTitle) {
        this.safeSend(ws, this.buildEvent({ kind: "meeting_title", text: this.lastMeetingTitle }));
      }
```
**아래**에 추가:
```typescript
      if (this.lastMeetingInfo) {
        this.safeSend(ws, this.buildEvent({ kind: "meeting_info", text: this.lastMeetingInfo }));
      }
```

- [ ] **Step 7: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0. (typecheck 가 `evt.data`/`crypto.subtle` 로 불평하면 `evt: any` 로 콜백 인자 타입 완화 — 단, 우선 그대로 시도.)

- [ ] **Step 8: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(web/DO): creds 보관 + watch 첫-메시지 인증 게이트 + info 동기화

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: index.ts — /:name/meeting/:mid 라우트

**Files:** Modify `jarvis-web/src/index.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: 라우트 추가** — 기존 `app.get("/:name/meeting", (c) => { ... });` 블록 **아래**에:
```typescript
app.get("/:name/meeting/:mid", (c) => {
  return new Response(VIEWER_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```
(`/watch/:key` 라우트는 변경 없음 — key=room, mid·pw 는 auth 메시지로 전달.)

- [ ] **Step 2: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/index.ts
git commit -m "feat(web): /:name/meeting/:mid 자막 페이지 라우트

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: viewer.html — 비번 게이트 + 첫-메시지 인증

**Files:** Modify `jarvis-web/src/static/viewer.html`. 검증: JS 구문.

- [ ] **Step 1: 게이트 CSS 추가** — `<style>` 블록 끝(닫는 `</style>` 직전)에:
```css
  #gate { position: fixed; inset: 0; background: var(--bg, #ffffff); display: flex;
    align-items: center; justify-content: center; z-index: 100; }
  #gate.hidden { display: none; }
  .gate-box { display: flex; flex-direction: column; gap: 10px; width: min(86vw, 300px); }
  .gate-title { font-size: 16px; font-weight: 600; }
  #gate-pw { font-size: 16px; padding: 10px; border: 1px solid #888; border-radius: 8px; }
  #gate-go { font-size: 15px; padding: 10px; border-radius: 8px; cursor: pointer; }
  #gate-err { color: #c00; font-size: 13px; min-height: 16px; }
```

- [ ] **Step 2: 게이트 HTML 추가** — `<body>` 의 `<header>` **위**에:
```html
  <div id="gate">
    <div class="gate-box">
      <div class="gate-title">🔒 회의 자막 입장</div>
      <input id="gate-pw" type="password" placeholder="비번" />
      <button id="gate-go">입장</button>
      <div id="gate-err"></div>
    </div>
  </div>
```

- [ ] **Step 3: key+mid 추출 교체** — `<script>` 안의
```javascript
  const key = decodeURIComponent(location.pathname.replace(/^\//, "").replace(/\/.*$/, "")) || "jarvis";
```
를 교체:
```javascript
  const _parts = location.pathname.split("/").filter(Boolean);
  const key = decodeURIComponent(_parts[0] || "jarvis");
  const mid = decodeURIComponent(_parts[2] || "");
```

- [ ] **Step 4: pw 변수 + open 시 auth 전송 + 4003 처리**

(a) `let ws = null, reconnectDelay = 500;` 를 교체:
```javascript
  let ws = null, reconnectDelay = 500, pw = "";
```
(b) `connect()` 안의 **연속된 세 줄**(`open` 핸들러, 그 다음 `message` 핸들러 한 줄, 그 다음 `close` 핸들러)을 아래 블록으로 통째 교체한다. 바로 아래의 `error` 핸들러 줄은 그대로 둔다:
```javascript
    ws.addEventListener("open", () => {
      $("conn").textContent = "● live"; $("conn").className = "ok"; reconnectDelay = 500;
      ws.send(JSON.stringify({ kind: "auth", mid, pw }));
    });
    ws.addEventListener("message", (m) => { try { handle(JSON.parse(m.data)); } catch (e) {} });
    ws.addEventListener("close", (e) => {
      if (e.code === 4003) {                       // 게이트 거부 — 재입력
        pw = "";
        $("gate").classList.remove("hidden");
        $("gate-err").textContent = "입장할 수 없습니다 (비번/회의 확인).";
        $("conn").textContent = "🔒"; $("conn").className = "bad";
        return;
      }
      $("conn").textContent = `재연결 (${reconnectDelay/1000}s)…`; $("conn").className = "bad";
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 8000);
    });
```
(결과적으로 `message` 핸들러는 한 번만 남고, `error` 핸들러는 보존된다.)

- [ ] **Step 5: 자동 connect → 게이트 제출로 교체** — `<script>` 맨 끝의 `connect();` 를 교체:
```javascript
  $("gate-go").addEventListener("click", () => {
    pw = $("gate-pw").value;
    if (!pw) { $("gate-err").textContent = "비번을 입력하세요"; return; }
    $("gate-err").textContent = "";
    $("gate").classList.add("hidden");
    connect();
  });
  $("gate-pw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("gate-go").click(); });
```

- [ ] **Step 6: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
```
Expected: `viewer JS OK`.

- [ ] **Step 7: 커밋**
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): 자막 페이지 비번 게이트 + 첫-메시지 인증

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: app.html — 폼 비번 입력 + owner 공유 줄

**Files:** Modify `jarvis-web/src/static/app.html`. 검증: JS 구문 + `npm run typecheck`.

- [ ] **Step 1: 폼에 비번 input 추가** — `#meeting-form` 의 워드북 row(`<div class="row"><div>워드북 ...`) **아래**에:
```html
      <div class="row"><div>비번 (선택 — 비우면 자동)</div><input id="mf-pass" type="text" placeholder="비번" /></div>
```

- [ ] **Step 2: 공유 줄 element + CSS**

(a) `#meeting-view` 안 `<main id="log">` **위**에:
```html
    <div id="meeting-share" class="hidden"></div>
```
(b) `<style>` 끝(닫는 `</style>` 직전)에:
```css
  #meeting-share { padding: 8px 12px; font-size: 13px; border-bottom: 1px solid var(--border);
    color: var(--muted); display: flex; gap: 8px; align-items: center; word-break: break-all; }
  #meeting-share.hidden { display: none; }
  #meeting-share button { font-size: 12px; padding: 4px 8px; white-space: nowrap; }
```

- [ ] **Step 3: menu-meet 리셋에 mf-pass 추가** — `$("menu-meet").addEventListener` 핸들러의 `$("mf-title").value = ""; $("mf-vocab").value = "";` 를 교체:
```javascript
    $("mf-title").value = ""; $("mf-vocab").value = ""; $("mf-pass").value = "";
```

- [ ] **Step 4: mf-start 가 password 전송** — `mf-start` 클릭 핸들러를 교체:
```javascript
  $("mf-start").addEventListener("click", () => {
    const title = $("mf-title").value.trim();
    const vocab = $("mf-vocab").value.split(",").map((s) => s.trim()).filter(Boolean);
    const password = $("mf-pass").value.trim();
    $("meeting-form").classList.add("hidden");
    showMeetingLoading();
    sendControl({ kind: "meeting_start", title, vocabulary: vocab, password });
  });
```

- [ ] **Step 5: meeting_info handle + navigate 정리**

(a) `handle()` 의 `case "meeting_title":` 블록 **아래**에 추가:
```javascript
      case "meeting_info": {
        try {
          const info = JSON.parse(ev.text || "{}");
          const room = location.pathname.split("/").filter(Boolean)[0] || "jarvis";
          const link = `${location.origin}/${encodeURIComponent(room)}/meeting/${encodeURIComponent(info.meeting_id || "")}`;
          const share = `${link} (비번: ${info.password || ""})`;
          const el = $("meeting-share");
          el.innerHTML = `<span>🔗 ${link} · 🔒 ${info.password || ""}</span>`;
          const btn = document.createElement("button");
          btn.textContent = "복사";
          btn.addEventListener("click", () => { navigator.clipboard?.writeText(share); });
          el.appendChild(btn);
          el.classList.remove("hidden");
        } catch {}
        return;
      }
```
(b) `case "navigate":` 블록에 공유 줄 숨김 추가 — 기존:
```javascript
      case "navigate":
        showView(ev.text); mic.apply();
        if (ev.text === "meeting") hideMeetingLoading();
        return;
```
교체:
```javascript
      case "navigate":
        showView(ev.text); mic.apply();
        if (ev.text === "meeting") hideMeetingLoading();
        else $("meeting-share").classList.add("hidden");
        return;
```

- [ ] **Step 6: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('app JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `app JS OK`, typecheck exit 0.

- [ ] **Step 7: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/app.html
git commit -m "feat(web): 회의 폼 비번 입력 + owner 공유 링크/비번 표시

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, live_translate; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');for(const f of ['jarvis-web/src/static/app.html','jarvis-web/src/static/viewer.html']){const h=fs.readFileSync(f,'utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(f,e.message);process.exit(1);}}}console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포/재시작 후)**
- 웹 +메뉴 미팅 → 폼에 제목·워드북·비번 입력(또는 비번 비움) → 시작. 콘솔에 `🔑 회의 ID` · `🔒 비번`. 웹 회의 헤더 아래 공유 줄(링크+비번+복사).
- `/meet` 콘솔 → 제목 → 워드북 → 비번(Enter=자동) 프롬프트.
- 새 탭에서 `/{room}/meeting/{id}` 열기 → 비번 입력창 → 정답 → 자막 스트림. 오답/빈칸 → "입장할 수 없습니다". 없는 회의(종료 후) → 거부.
- 회의 종료 후 같은 링크 재입장 시도 → 거부(creds 초기화).

---

## 비고
- 첫-메시지 인증: viewer 가 open 직후 `{kind:"auth",mid,pw}` 전송, DO 가 sha256 검증. 통과 전엔 viewer 맵 미등록 → 자막 미수신.
- meeting_creds/meeting_info 는 PUBLIC_KINDS 밖 → 공개 viewer 미수신(비번 평문 안전).
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
