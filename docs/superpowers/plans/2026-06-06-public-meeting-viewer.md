# SP4 — 공개 view-only /meeting 뷰어 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/{name}/meeting` 을 무인증 공개 자막 뷰어(읽기 전용)로 만들고, 공개 뷰어에는 자막만(내 채팅·TTS·제어 제외) 흐르게 한다.

**Architecture:** DO 의 viewer 를 owner/public 역할로 구분, public 에는 자막 kind 만 broadcast + replay 없음. 새 무인증 `/watch/:key` 라우트 + `/{name}/meeting` 이 신규 `viewer.html` 서빙. 소유자 SPA(app.html)는 회의 진입 시 URL 안 바꿈.

**Tech Stack:** Cloudflare Worker(TS, Hono, Durable Object) · 바닐라 JS/HTML.

전제: `cd /Users/oracle/Documents/concode/jarvis`. typecheck: `cd jarvis-web && npm run typecheck`. 로컬: `cd jarvis-web && npx wrangler dev --port 8787`.

---

## Task 1: meeting_do.ts — viewer 역할 + 필터

**Files:** Modify `jarvis-web/src/meeting_do.ts`

- [ ] **Step 1: PUBLIC_KINDS 상수** — `interface Env { ... }` 블록 다음, `export class MeetingDO {` 앞에 추가. 현재:
```ts
interface Env {
  // 빈 인터페이스이지만 DO 컨텍스트용
}

export class MeetingDO {
```
교체:
```ts
interface Env {
  // 빈 인터페이스이지만 DO 컨텍스트용
}

// 공개(public) 뷰어에게 보내도 되는 자막 계열 kind. 그 외(user/assistant/navigate/mic_source)·
// 바이너리 TTS 는 owner 전용.
const PUBLIC_KINDS = new Set([
  "hello", "source", "translation_ko", "translation_en", "partial",
  "gap", "info", "end", "kicked", "publisher_disconnected",
]);

export class MeetingDO {
```

- [ ] **Step 2: viewers 를 Map 으로** — 현재:
```ts
  private viewers: Set<WebSocket> = new Set();
```
교체:
```ts
  private viewers: Map<WebSocket, "owner" | "public"> = new Map();
```

- [ ] **Step 3: fetch role 화이트리스트 + 분기** — 현재 화이트리스트(40행):
```ts
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv" && role !== "control" && role !== "control-recv") {
```
교체:
```ts
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv" && role !== "control" && role !== "control-recv" && role !== "watch") {
```
그리고 분기 — 현재:
```ts
    } else if (role === "subscribe") {
      this.attachViewer(server);
    } else if (role === "mic") {
```
교체:
```ts
    } else if (role === "subscribe") {
      this.attachViewer(server, "owner");
    } else if (role === "watch") {
      this.attachViewer(server, "public");
    } else if (role === "mic") {
```

- [ ] **Step 4: attachViewer(role) + replay owner 한정** — 현재:
```ts
  private attachViewer(ws: WebSocket): void {
    this.viewers.add(ws);
    // replay: 최근 events 그대로 1회 전송
    for (const ev of this.events) {
      this.safeSend(ws, ev);
    }
    if (this.lastMicSource) {
      this.safeSend(ws, this.buildEvent({ kind: "mic_source", source: this.lastMicSource as "system" | "remote" }));
    }
    ws.addEventListener("close", () => {
      this.viewers.delete(ws);
    });
    ws.addEventListener("error", () => {
      this.viewers.delete(ws);
    });
  }
```
교체:
```ts
  private attachViewer(ws: WebSocket, role: "owner" | "public"): void {
    this.viewers.set(ws, role);
    // replay 는 owner 만 (public = 라이브만, 과거 자막 미노출)
    if (role === "owner") {
      for (const ev of this.events) {
        this.safeSend(ws, ev);
      }
      if (this.lastMicSource) {
        this.safeSend(ws, this.buildEvent({ kind: "mic_source", source: this.lastMicSource as "system" | "remote" }));
      }
    }
    ws.addEventListener("close", () => {
      this.viewers.delete(ws);
    });
    ws.addEventListener("error", () => {
      this.viewers.delete(ws);
    });
  }
```

- [ ] **Step 5: broadcast/broadcastBinary 필터** — 현재:
```ts
  private broadcast(ev: RelayEvent): void {
    for (const ws of this.viewers) {
      this.safeSend(ws, ev);
    }
  }

  private broadcastBinary(data: ArrayBuffer): void {
    for (const ws of this.viewers) {
      try { ws.send(data); } catch { /* 끊긴 소켓 — close 에서 정리 */ }
    }
  }
```
교체:
```ts
  private broadcast(ev: RelayEvent): void {
    for (const [ws, role] of this.viewers) {
      if (role === "public" && !PUBLIC_KINDS.has(ev.kind)) continue;   // public 은 자막만
      this.safeSend(ws, ev);
    }
  }

  private broadcastBinary(data: ArrayBuffer): void {
    for (const [ws, role] of this.viewers) {
      if (role !== "owner") continue;   // TTS 오디오는 owner 만
      try { ws.send(data); } catch { /* 끊긴 소켓 — close 에서 정리 */ }
    }
  }
```

- [ ] **Step 6: 타입체크 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `cd /Users/oracle/Documents/concode/jarvis && grep -c 'PUBLIC_KINDS\|"owner"\|"public"' jarvis-web/src/meeting_do.ts` → `6` 이상
```bash
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(SP4,DO): viewer owner/public 역할 + public 자막만 필터(replay/TTS 제외)"
```

---

## Task 2: viewer.html — 공개 자막 뷰어 (신규)

**Files:** Create `jarvis-web/src/static/viewer.html`

- [ ] **Step 1: 파일 생성** — `jarvis-web/src/static/viewer.html` 을 아래 내용 그대로:
```html
<!-- jarvis-web/src/static/viewer.html — 공개 회의 자막 뷰어 (무인증, 읽기 전용) -->
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>회의 자막</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #ffffff; --fg: #111418; --muted: #6b7280; --card: #f5f6f8;
    --border: #e5e7eb; --accent: #2563eb; --ko: #0a7e0a; --en: #b04300; --partial: #9aa0a6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0e1116; --fg: #e8eaed; --muted: #9aa0a6; --card: #161a21;
      --border: #2a2f37; --accent: #60a5fa; --ko: #7ee787; --en: #ffb86b; --partial: #6b7280;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "Noto Sans KR", Arial, sans-serif;
    min-height: 100vh; }
  header { position: sticky; top: 0; z-index: 10; padding: 12px 16px;
    border-bottom: 1px solid var(--border); background: var(--bg);
    display: flex; align-items: center; gap: 12px; }
  header .title { font-size: 18px; font-weight: 600; }
  #conn { margin-left: auto; font-size: 13px; opacity: 0.8; }
  #conn.ok { color: var(--ko); opacity: 1; }
  #conn.bad { color: #d33; opacity: 1; }
  #log { padding: 16px 16px 40px; max-width: 980px; margin: 0 auto; width: 100%; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin: 10px 0; }
  .card.draft { background: transparent; border: 1px dashed var(--border); color: var(--partial); }
  .card.draft .src { color: var(--partial); }
  .src { font-size: 18px; }
  .tx { font-size: 17px; margin-top: 6px; }
  .tx.ko { color: var(--ko); }
  .tx.en { color: var(--en); }
  .info { color: var(--muted); font-size: 13px; }
  .draft .src::after { content: "▍"; display: inline-block; margin-left: 4px; animation: blink 1s steps(2, start) infinite; opacity: 0.6; }
  @keyframes blink { to { visibility: hidden; } }
  .lockbar { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
    background: var(--accent); color: white; padding: 6px 14px; border-radius: 999px;
    font-size: 13px; cursor: pointer; opacity: 0; pointer-events: none; transition: opacity .2s; }
  .lockbar.show { opacity: 1; pointer-events: auto; }
  @media (max-width: 600px) { .src { font-size: 17px; } .tx { font-size: 16px; } }
</style>
</head>
<body>
  <header>
    <div class="title" id="title">🎤 회의 자막</div>
    <div id="conn">···</div>
  </header>
  <main id="log"></main>
  <div class="lockbar" id="lockbar">↓ 새 자막 보기</div>
<script>
(() => {
  const key = decodeURIComponent(location.pathname.replace(/^\//, "").replace(/\/.*$/, "")) || "jarvis";
  const $ = (id) => document.getElementById(id);
  let lastCard = null, draftCard = null, lockedToBottom = true;
  window.addEventListener("scroll", () => {
    const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    lockedToBottom = nearBottom;
    $("lockbar").classList.toggle("show", !nearBottom);
  });
  $("lockbar").addEventListener("click", () => {
    lockedToBottom = true;
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    $("lockbar").classList.remove("show");
  });
  function scrollIfLocked() { if (lockedToBottom) window.scrollTo({ top: document.body.scrollHeight }); }
  function applyMeta(meta) {
    if (!meta) return;
    const user = meta.user || "?";
    $("title").textContent = meta.partner ? `🎤 ${meta.partner} ↔ ${user}` : "🎤 Meeting";
  }
  function newCard() {
    const card = document.createElement("section");
    card.className = "card"; $("log").appendChild(card); return card;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function handle(ev) {
    switch (ev.kind) {
      case "hello": applyMeta(ev.meta); return;
      case "info": { const c = newCard(); c.innerHTML = `<div class="info">${escapeHtml(ev.text || "")}</div>`; lastCard = null; break; }
      case "source": {
        let card;
        if (draftCard) { card = draftCard; card.classList.remove("draft"); card.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`; draftCard = null; }
        else { card = newCard(); card.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`; }
        lastCard = card; break;
      }
      case "translation_ko":
      case "translation_en": {
        const cls = ev.kind === "translation_ko" ? "ko" : "en";
        const sym = ev.kind === "translation_ko" ? "🌐" : "🇺🇸";
        const html = `<div class="tx ${cls}">${sym} ${escapeHtml(ev.text || "")}</div>`;
        if (lastCard) lastCard.insertAdjacentHTML("beforeend", html);
        else { const c = newCard(); c.innerHTML = html; lastCard = c; }
        break;
      }
      case "partial": {
        if (!draftCard) { draftCard = newCard(); draftCard.classList.add("draft"); }
        draftCard.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`;
        scrollIfLocked(); return;
      }
      case "gap": lastCard = null; if (draftCard) { draftCard.remove(); draftCard = null; } return;
      case "end": { const c = newCard(); c.innerHTML = `<div class="info">— 회의 종료 —</div>`; $("conn").textContent = "🛑 종료됨"; $("conn").className = "bad"; break; }
      case "kicked": { const c = newCard(); c.innerHTML = `<div class="info">— 새 publisher 가 채널을 인수했습니다 —</div>`; break; }
      case "publisher_disconnected": { $("conn").textContent = "⏸ 연결 끊김"; $("conn").className = "bad"; return; }
      default: return;
    }
    scrollIfLocked();
  }
  let ws = null, reconnectDelay = 500;
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/watch/${encodeURIComponent(key)}`);
    ws.addEventListener("open", () => { $("conn").textContent = "● live"; $("conn").className = "ok"; reconnectDelay = 500; });
    ws.addEventListener("message", (m) => { try { handle(JSON.parse(m.data)); } catch (e) {} });
    ws.addEventListener("close", () => {
      $("conn").textContent = `재연결 (${reconnectDelay/1000}s)…`; $("conn").className = "bad";
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 8000);
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch {} });
  }
  connect();
})();
</script>
</body>
</html>
```

- [ ] **Step 2: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/viewer.html > /tmp/viewer.js && node --check /tmp/viewer.js && echo "JS OK"` → `JS OK`
`grep -c '/watch/\|getElementById\|로그인\|login\|micStart' jarvis-web/src/static/viewer.html` → `/watch/` 와 getElementById 는 있고 login/micStart 는 없어야 함(공개·무입력 확인). 직접: `grep -c 'login\|micStart\|getUserMedia' jarvis-web/src/static/viewer.html` → `0`
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(SP4): viewer.html — 무인증 공개 회의 자막 뷰어"
```

---

## Task 3: index.ts — /watch 라우트 + /meeting → viewer.html

**Files:** Modify `jarvis-web/src/index.ts`

- [ ] **Step 1: VIEWER_HTML import** — 현재 HTML import:
```ts
import APP_HTML from "./static/app.html";
```
다음에 추가:
```ts
import VIEWER_HTML from "./static/viewer.html";
```

- [ ] **Step 2: forwardToDO role 유니온** — 현재:
```ts
function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv" | "control" | "control-recv", original: Request): Promise<Response> {
```
교체:
```ts
function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv" | "control" | "control-recv" | "watch", original: Request): Promise<Response> {
```

- [ ] **Step 3: /watch 라우트(무인증)** — `/control-recv/:key` 핸들러(닫는 `});`) 다음, `/control/:key`·`/control-recv/:key` 근처(어쨌든 `/:name/meeting` 핸들러 **앞**)에 삽입:
```ts
app.get("/watch/:key", async (c) => {
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "watch", c.req.raw);
});
```

- [ ] **Step 4: /{name}/meeting → VIEWER_HTML** — 현재:
```ts
app.get("/:name/meeting", (c) => {
  return new Response(APP_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```
교체:
```ts
app.get("/:name/meeting", (c) => {
  return new Response(VIEWER_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```
(`/:name` 은 `APP_HTML` 그대로.)

- [ ] **Step 5: 검증 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `cd /Users/oracle/Documents/concode/jarvis && grep -c 'VIEWER_HTML\|/watch/' jarvis-web/src/index.ts` → `4` 이상 (import 1 + meeting 라우트 1 + watch 라우트 1 + forwardToDO 호출 1)
```bash
git add jarvis-web/src/index.ts
git commit -m "feat(SP4): /watch 무인증 라우트 + /{name}/meeting → viewer.html"
```

---

## Task 4: app.html — showView URL 분리

**Files:** Modify `jarvis-web/src/static/app.html`

소유자 SPA 는 회의 진입 시 URL 을 바꾸지 않는다(/{name} 유지). /meeting 은 이제 공개 뷰어이므로.

- [ ] **Step 1: showView pushState 제거 + popstate/pathIsMeeting 정리** — 현재:
```js
  function pathIsMeeting() { return /\/meeting\/?$/.test(location.pathname); }
  function setView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    document.body.dataset.view = nv;
    $("title").textContent = nv === "meeting" ? meetingTitle : "🤖 Jarvis";
    $("plus-menu").classList.add("hidden");   // 뷰 전환 시 메뉴 닫기
  }
  function showView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    if (document.body.dataset.view === nv) return;
    setView(nv);
    const path = nv === "meeting" ? `/${encodeURIComponent(name)}/meeting` : `/${encodeURIComponent(name)}`;
    history.pushState({ view: nv }, "", path);
  }
  window.addEventListener("popstate", () => setView(pathIsMeeting() ? "meeting" : "home"));
```
교체(pathIsMeeting·pushState·popstate 제거 — app.html 은 /{name} 에서만 로드):
```js
  function setView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    document.body.dataset.view = nv;
    $("title").textContent = nv === "meeting" ? meetingTitle : "🤖 Jarvis";
    $("plus-menu").classList.add("hidden");   // 뷰 전환 시 메뉴 닫기
  }
  function showView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    if (document.body.dataset.view === nv) return;
    setView(nv);   // URL 은 /{name} 유지 — /meeting 은 공개 뷰어
  }
```

- [ ] **Step 2: 초기화 setView 고정** — 현재:
```js
  setView(pathIsMeeting() ? "meeting" : "home");
```
교체:
```js
  setView("home");
```

- [ ] **Step 3: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/appsp4.js && node --check /tmp/appsp4.js && echo "JS OK"` → `JS OK`
`grep -c 'pathIsMeeting\|popstate\|pushState' jarvis-web/src/static/app.html` → `0`
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(SP4,jarvis-web): showView 가 URL 안 바꿈(소유자 /{name} 유지)"
```

---

## Task 5: 통합 필터 검증 + 배포

**Files:** Modify `jarvis-web/scripts/mic_relay_check.mjs`

- [ ] **Step 1: public 필터 검증 추가** — `mic_relay_check.mjs` 의 `main()`, 마지막 정리(`[recv, send, send2, viewer, viewer2].forEach(...)`) **직전**에 삽입:
```javascript
  // 13) 공개 watch 뷰어: 자막(source)만, 채팅(assistant)·무인증
  const watchV = await open(`${BASE}/watch/${KEY}`);                 // 무인증 OK
  const subV = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  const wMsgs = [], sMsgs = [];
  watchV.on("message", (d, isB) => { if (!isB) wMsgs.push(d.toString()); });
  subV.on("message", (d, isB) => { if (!isB) sMsgs.push(d.toString()); });
  const pub3 = await open(`${BASE}/publish/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });
  pub3.send(JSON.stringify({ kind: "source", text: "공개자막" }));
  pub3.send(JSON.stringify({ kind: "assistant", text: "사적대화" }));
  await new Promise((r) => setTimeout(r, 600));
  const watchOk = wMsgs.some((s) => s.includes('"source"') && s.includes("공개자막")) && !wMsgs.some((s) => s.includes('"assistant"'));
  const subOk = sMsgs.some((s) => s.includes('"assistant"') && s.includes("사적대화"));
  console.log("public watch 자막만:", watchOk ? "OK" : `FAIL (${wMsgs.length} msgs)`);
  console.log("owner subscribe 전체:", subOk ? "OK" : "FAIL");
  pub3.close(); watchV.close(); subV.close();
```

- [ ] **Step 2: node --check** — `node --check jarvis-web/scripts/mic_relay_check.mjs` → SYNTAX OK.

- [ ] **Step 3: best-effort 라이브 런** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s, "Ready") → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 모든 줄 OK 기대(신규 "public watch 자막만: OK", "owner subscribe 전체: OK") → 추가로 `curl -s http://localhost:8787/Concode/meeting | grep -c '회의 자막'` ≥1, `curl -s http://localhost:8787/Concode | grep -c 'voice-toggle'` ≥1 (라우트 분리 확인) → `pkill -f "wrangler dev"`. 안 뜨면 스킵+사유.

- [ ] **Step 4: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/scripts/mic_relay_check.mjs
git commit -m "test(SP4): public watch 자막만/owner 전체 필터 검증"
```

- [ ] **Step 5: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 시크릿 브라우저로 `https://.../Concode/meeting` → 로그인 없이 자막만(내 채팅·음성 안 보임). 소유자 홈에서 회의 시작 → 공개 뷰어에 자막 흐름, 종료 → "회의 종료". 소유자 홈 URL 은 /Concode 유지.

---

## Self-Review 결과

**Spec coverage:**
- 무인증 `/watch/:key` → Task 3 Step 3 ✓
- DO viewer owner/public + 자막만 필터 + replay/TTS 제외 → Task 1 ✓
- `/{name}/meeting` → viewer.html → Task 2 + Task 3 Step 4 ✓
- viewer.html(무인증·읽기전용·입력없음) → Task 2 ✓
- showView URL 분리(소유자 /{name} 유지) → Task 4 ✓
- public 필터 통합 검증 → Task 5 ✓
- 비범위(공개 인증/다시보기) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전(viewer.html 전체 포함). 빈칸 없음.

**Type/이름 consistency:** role `"watch"` ↔ forwardToDO 유니온 ↔ DO fetch 화이트리스트/분기 ↔ attachViewer "public". `PUBLIC_KINDS` 가 broadcast 필터에서 사용. `VIEWER_HTML` import↔/meeting 라우트. viewer.html `/watch/{key}` ↔ index `/watch/:key`. `/subscribe`=owner(replay+전체), `/watch`=public(자막만·replay없음) 일관.

**핵심 위험:** (1) viewers Set→Map 변경 — broadcast/broadcastBinary/attachViewer/close 모두 Map API 로 일치(Task1). (2) public 필터가 navigate·mic_source·user·assistant·binary 제외 → 사적정보·제어 누수 없음. (3) replay 없음으로 public 라이브만(과거 자막 미노출). (4) app.html 은 이제 /{name} 전용 — pathIsMeeting/popstate 제거, 초기 home 고정. (5) /{name}/meeting 직접 진입은 공개 뷰어(소유자 제어는 /{name}).
```
