# jarvis-web 골격 (A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `meeting-web` 을 `jarvis-web` 으로 리네임하고, `/{name}`(홈)·`/{name}/meeting`(회의)로 라우팅, 전체 프론트를 ADMIN_PASSWORD 로그인 게이트, mic-take 를 홈으로 옮긴다.

**Architecture:** Cloudflare Worker(Hono+DO). HTML 페이지 경로만 `/{name}` 스킴으로 바꾸고 WS 엔드포인트(`/subscribe`·`/publish`·`/mic`·`/mic-recv`)는 평면 유지(jarvis 측 무변경). `/subscribe` 에 ADMIN_PASSWORD 게이트 추가. 새 `home.html` 에 로그인+mic-take, `meeting.html` 은 뷰 전용으로 축소.

**Tech Stack:** Cloudflare Workers (Hono, TypeScript, Durable Objects), 바닐라 JS, wrangler.

전제: `cd /Users/oracle/Documents/concode/jarvis`. 리네임 후 경로는 `jarvis-web/`. typecheck: `cd jarvis-web && npm run typecheck`.

---

## Task 1: 디렉터리·worker 리네임

**Files:** `meeting-web/` → `jarvis-web/`, `jarvis-web/wrangler.toml`

- [ ] **Step 1: git mv**

```bash
cd /Users/oracle/Documents/concode/jarvis
git mv meeting-web jarvis-web
```

- [ ] **Step 2: wrangler.toml 의 worker 이름 변경**

`jarvis-web/wrangler.toml` 의 첫 줄:
```toml
name = "meeting-web-jarvis"
```
을
```toml
name = "jarvis-web"
```
로 변경. (그 외 줄은 그대로 — DO 클래스 `MeetingDO`/migration 태그 유지.)

- [ ] **Step 3: 타입체크**

Run: `cd jarvis-web && npm install && npm run typecheck`
Expected: 오류 없음.

- [ ] **Step 4: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add -A
git commit -m "refactor: meeting-web → jarvis-web 리네임 (worker 이름 포함)"
```

---

## Task 2: index.ts — /{name} 라우팅 + /subscribe 게이트

**Files:** `jarvis-web/src/index.ts`

- [ ] **Step 1: 라우트 주석 + home.html import + 라우트 교체**

(a) 상단 주석 블록의 라우트 목록을 교체:
```
 *   GET  /healthz             상태 확인
 *   GET  /:name               홈 HTML (정적, 로그인)
 *   GET  /:name/meeting       회의 자막 뷰 HTML (정적, 로그인)
 *   GET  /subscribe/:key      WebSocket: viewer (ADMIN_PASSWORD 필요)
 *   GET  /publish/:key        WebSocket: publisher=jarvis (RELAY_TOKEN 필요)
 *   GET  /mic/:key            WebSocket: 마이크 송신 (ADMIN_PASSWORD 필요)
 *   GET  /mic-recv/:key       WebSocket: 마이크 수신=jarvis (RELAY_TOKEN 필요)
```

(b) `import MEETING_HTML from "./static/meeting.html";` 아래에 추가:
```typescript
import HOME_HTML from "./static/home.html";
```

(c) `app.get("/m/:key", ...)` 라우트 블록 전체를 다음 두 라우트로 교체:
```typescript
app.get("/:name/meeting", (c) => {
  return new Response(MEETING_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});

app.get("/:name", (c) => {
  return new Response(HOME_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```
**중요:** 이 두 `/:name*` 라우트는 `/subscribe`·`/publish`·`/mic`·`/mic-recv`·`/healthz`
라우트들보다 **아래(뒤)에** 위치해야 한다(Hono 는 먼저 등록된 라우트가 우선). 현재 파일은
`/healthz`, `/subscribe/:key`, `/publish/:key`, `/mic/:key`, `/mic-recv/:key` 가 위에 있고
그 다음에 `/m/:key` 가 있었으므로, 그 자리(맨 아래, `app.notFound` 직전)에 위 두 라우트를
넣으면 된다. `/:name/meeting`(2세그먼트)을 `/:name`(1세그먼트)보다 먼저 등록.

- [ ] **Step 2: /subscribe 에 ADMIN_PASSWORD 게이트 추가**

`app.get("/subscribe/:key", ...)` 핸들러를 교체:
```typescript
app.get("/subscribe/:key", async (c) => {
  if (!requireAdmin(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") {
    return c.text("expected websocket", 426);
  }
  return forwardToDO(c.env, c.req.param("key"), "subscribe", c.req.raw);
});
```
(`requireAdmin`/`checkSecret` 헬퍼는 이미 파일에 있음.)

- [ ] **Step 3: 타입체크 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음 (home.html 은 Task 3 에서 생성하지만 `*.html` 텍스트 모듈 선언으로 tsc 는 통과; 런타임 번들은 Task 3 후).
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/index.ts
git commit -m "feat(jarvis-web): /:name·/:name/meeting 라우팅 + /subscribe 인증"
```

---

## Task 3: home.html — 로그인 + mic-take + 배지 + nav + 채팅 placeholder

**Files:** `jarvis-web/src/static/home.html` (신규)

- [ ] **Step 1: home.html 작성 (전체)**

```html
<!-- jarvis-web/src/static/home.html -->
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
<title>Jarvis</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; display: flex; flex-direction: column; height: 100vh; }
  header { display: flex; gap: 10px; align-items: center; padding: 10px 14px; border-bottom: 1px solid #8883; }
  header .title { font-weight: 600; }
  #mic-src { padding: 3px 8px; border-radius: 6px; background: #8882; font-size: 13px; }
  #mic-src.remote { background: #22c55e; color: #03240f; }
  #conn { margin-left: auto; font-size: 13px; opacity: 0.7; }
  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 10px 14px; border-bottom: 1px solid #8883; }
  button { font-size: 15px; padding: 8px 16px; border-radius: 10px; border: none; background: #2563eb; color: #fff; cursor: pointer; }
  button.off { background: #dc2626; }
  a.navbtn { font-size: 15px; padding: 8px 16px; border-radius: 10px; background: #6b7280; color: #fff; text-decoration: none; }
  #mic-level { width: 120px; height: 8px; background: #ddd4; border-radius: 4px; overflow: hidden; }
  #mic-bar { height: 100%; width: 0%; background: #22c55e; }
  #chat { flex: 1; padding: 16px; overflow-y: auto; opacity: 0.6; }
  #login { position: fixed; inset: 0; background: #000a; display: flex; flex-direction: column;
           gap: 12px; align-items: center; justify-content: center; z-index: 10; }
  #login input { font-size: 16px; padding: 10px; width: 240px; border-radius: 8px; border: 1px solid #888; }
  .hidden { display: none !important; }
</style>
</head>
<body>
  <div id="login">
    <div>🔒 Jarvis 로그인</div>
    <input id="login-pw" type="password" placeholder="password" />
    <button id="login-go">들어가기</button>
  </div>

  <header>
    <span class="title">🤖 Jarvis</span>
    <span id="mic-src"></span>
    <span id="conn">···</span>
  </header>
  <div id="controls">
    <button id="mic-toggle">🎙️ 마이크 켜기</button>
    <div id="mic-level" class="hidden"><div id="mic-bar"></div></div>
    <a class="navbtn" id="to-meeting" href="#">🗣️ 회의 모드로</a>
  </div>
  <main id="chat">💬 음성 대화는 다음 단계에서 제공됩니다.</main>

<script>
(() => {
  const ADMIN_KEY = "jarvis_admin_pw";
  const TARGET_SR = 16000;
  const name = decodeURIComponent(location.pathname.replace(/^\//, "").replace(/\/.*$/, "")) || "jarvis";
  const $ = (id) => document.getElementById(id);
  document.getElementById("to-meeting").href = `/${encodeURIComponent(name)}/meeting`;

  function getPw() { return localStorage.getItem(ADMIN_KEY) || ""; }
  function showLogin() { $("login").classList.remove("hidden"); }
  function hideLogin() { $("login").classList.add("hidden"); }
  $("login-go").addEventListener("click", () => {
    const pw = $("login-pw").value.trim();
    if (!pw) return;
    localStorage.setItem(ADMIN_KEY, pw);
    hideLogin();
    connect();
  });
  if (getPw()) hideLogin(); else showLogin();

  // ---- 자막/상태 구독 (소스 배지) ----
  let ws = null, reconnectDelay = 500;
  function connect() {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/subscribe/${encodeURIComponent(name)}?token=${encodeURIComponent(pw)}`);
    ws.addEventListener("open", () => { $("conn").textContent = "● live"; reconnectDelay = 500; });
    ws.addEventListener("message", (m) => {
      try {
        const ev = JSON.parse(m.data);
        if (ev.kind === "mic_source") {
          $("mic-src").textContent = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
          $("mic-src").classList.toggle("remote", ev.source === "remote");
        }
      } catch {}
    });
    ws.addEventListener("close", (e) => {
      if (e.code === 1006 || e.code === 1008) {
        localStorage.removeItem(ADMIN_KEY);
        $("conn").textContent = "인증 필요";
        showLogin();
        return;
      }
      $("conn").textContent = `재연결 (${reconnectDelay/1000}s)…`;
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 8000);
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch {} });
  }
  if (getPw()) connect();

  // ---- mic-take (홈으로 이동) ----
  let micWS = null, micCtx = null, micNode = null, micStream = null, micOn = false, wakeLock = null;
  function downsample(input, inRate) {
    if (inRate === TARGET_SR) return input;
    const ratio = inRate / TARGET_SR, outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) out[i] = input[Math.floor(i * ratio)];
    return out;
  }
  function floatToInt16(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  async function requestWakeLock() {
    try { if ("wakeLock" in navigator) { wakeLock = await navigator.wakeLock.request("screen"); wakeLock.addEventListener("release", () => { wakeLock = null; }); } } catch {}
  }
  document.addEventListener("visibilitychange", () => {
    if (micOn && wakeLock === null && document.visibilityState === "visible") requestWakeLock();
  });
  function loseMic(msg) {
    micStop(); micOn = false; $("mic-toggle").textContent = "🎙️ 마이크 켜기"; $("mic-toggle").classList.remove("off");
    if (msg) alert(msg);
  }
  async function micStart() {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    micWS = new WebSocket(`${proto}//${location.host}/mic/${encodeURIComponent(name)}?token=${encodeURIComponent(pw)}`);
    micWS.binaryType = "arraybuffer";
    micWS.onopen = () => micWS.send(JSON.stringify({ kind: "mic_start" }));
    micWS.onmessage = (e) => { try { if (JSON.parse(e.data).kind === "kicked") loseMic("다른 기기가 마이크를 가져갔습니다."); } catch {} };
    micWS.onclose = (e) => {
      if (e.code === 1006 || e.code === 1008) { localStorage.removeItem(ADMIN_KEY); showLogin(); loseMic("인증 실패 — 다시 로그인하세요."); }
      else if (micOn) loseMic("마이크 연결이 종료되었습니다.");
    };
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    micCtx = new (window.AudioContext || window.webkitAudioContext)();
    const srcNode = micCtx.createMediaStreamSource(micStream);
    micNode = micCtx.createScriptProcessor(4096, 1, 1);
    srcNode.connect(micNode); micNode.connect(micCtx.destination);
    $("mic-level").classList.remove("hidden");
    await requestWakeLock();
    micNode.onaudioprocess = (ev) => {
      const inp = ev.inputBuffer.getChannelData(0);
      if (!micWS || micWS.readyState !== 1) { $("mic-bar").style.width = "0%"; return; }
      let peak = 0; for (let i = 0; i < inp.length; i++) peak = Math.max(peak, Math.abs(inp[i]));
      $("mic-bar").style.width = Math.min(100, peak * 140) + "%";
      micWS.send(floatToInt16(downsample(inp, micCtx.sampleRate)).buffer);
    };
  }
  function micStop() {
    try { if (micWS && micWS.readyState === 1) micWS.send(JSON.stringify({ kind: "mic_stop" })); } catch {}
    if (wakeLock) { try { wakeLock.release(); } catch {} wakeLock = null; }
    if (micNode) { micNode.disconnect(); micNode.onaudioprocess = null; micNode = null; }
    if (micCtx) { micCtx.close(); micCtx = null; }
    if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
    if (micWS) { try { micWS.close(); } catch {} micWS = null; }
    $("mic-bar").style.width = "0%"; $("mic-level").classList.add("hidden");
  }
  $("mic-toggle").addEventListener("click", async () => {
    micOn = !micOn;
    $("mic-toggle").textContent = micOn ? "🎙️ 마이크 끄기" : "🎙️ 마이크 켜기";
    $("mic-toggle").classList.toggle("off", micOn);
    if (micOn) { try { await micStart(); } catch (e) { alert("마이크 권한 실패: " + e.message); micOn = false; $("mic-toggle").textContent = "🎙️ 마이크 켜기"; $("mic-toggle").classList.remove("off"); } }
    else micStop();
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 2: 타입체크 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `grep -c 'id="login"' jarvis-web/src/static/home.html` → 1
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/home.html
git commit -m "feat(jarvis-web): 홈 — 로그인 + mic-take + 소스 배지 + 회의 nav"
```

---

## Task 4: meeting.html — 뷰 전용 (admin-bar 제거 + 로그인 + repath)

**Files:** `jarvis-web/src/static/meeting.html`

먼저 `jarvis-web/src/static/meeting.html` 전체를 읽는다. 아래 4개 변경을 적용한다.

- [ ] **Step 1: admin-bar HTML 제거**

`<body>` 의 `<header>...</header>` 다음에 있는 admin-bar 블록 전체를 삭제:
```html
  <div id="admin-bar">
    <button id="admin-toggle">🔒 admin</button>
    <input id="admin-pw" type="password" placeholder="admin password" class="admin-hidden" />
    <button id="admin-unlock" class="admin-hidden">잠금 해제</button>
    <button id="mic-toggle" class="admin-hidden">🎙️ 마이크 켜기</button>
    <div id="mic-level" class="admin-hidden"><div id="mic-bar"></div></div>
    <span id="mic-src"></span>
  </div>
```
대신 소스 배지만 헤더에 남긴다 — `<header>` 안에 `<span id="mic-src"></span>` 를 추가
(예: `<div class="conn" id="conn">···</div>` 앞 또는 뒤). 헤더 구조에 맞춰 한 줄 삽입.

- [ ] **Step 2: 로그인 오버레이 추가**

`<body>` 바로 다음에 로그인 오버레이를 삽입:
```html
  <div id="login" style="position:fixed;inset:0;background:#000a;display:flex;flex-direction:column;gap:12px;align-items:center;justify-content:center;z-index:10;">
    <div>🔒 Jarvis 로그인</div>
    <input id="login-pw" type="password" placeholder="password" style="font-size:16px;padding:10px;width:240px;border-radius:8px;border:1px solid #888;" />
    <button id="login-go" style="font-size:15px;padding:8px 16px;border-radius:10px;border:none;background:#2563eb;color:#fff;">들어가기</button>
  </div>
```

- [ ] **Step 3: key 추출 + subscribe 토큰 + 로그인 로직**

스크립트 IIFE 안에서:

(a) key 추출을 `/m/` 에서 `/:name/meeting` 의 첫 세그먼트로 변경. 현재:
```javascript
  const key = location.pathname.replace(/^\/m\//, "");
```
교체:
```javascript
  const ADMIN_KEY = "jarvis_admin_pw";
  const key = decodeURIComponent(location.pathname.replace(/^\//, "").replace(/\/.*$/, "")) || "jarvis";
  const getPw = () => localStorage.getItem(ADMIN_KEY) || "";
  const loginEl = document.getElementById("login");
  function showLogin() { loginEl.style.display = "flex"; }
  function hideLogin() { loginEl.style.display = "none"; }
  document.getElementById("login-go").addEventListener("click", () => {
    const pw = document.getElementById("login-pw").value.trim();
    if (!pw) return;
    localStorage.setItem(ADMIN_KEY, pw); hideLogin(); connect();
  });
  if (getPw()) hideLogin(); else showLogin();
```

(b) subscribe 연결에 토큰 추가 + 인증 실패 처리. 현재:
```javascript
    ws = new WebSocket(`${proto}//${location.host}/subscribe/${encodeURIComponent(key)}`);
```
교체:
```javascript
    if (!getPw()) { showLogin(); return; }
    ws = new WebSocket(`${proto}//${location.host}/subscribe/${encodeURIComponent(key)}?token=${encodeURIComponent(getPw())}`);
```
그리고 `ws.addEventListener("close", ...)` 안 맨 앞에 인증실패 분기 추가:
```javascript
      if (event.code === 1006 || event.code === 1008) {
        localStorage.removeItem(ADMIN_KEY); showLogin();
        connEl.textContent = "인증 필요"; connEl.className = "conn bad"; return;
      }
```
(기존 재연결 로직은 그 뒤에 둔다.)

(c) 맨 아래의 `connect()` 자동 호출은 로그인된 경우에만. 현재 끝부분:
```javascript
  if (!key) {
    titleEl.textContent = "회의 키가 URL 에 없습니다.";
  } else {
    connect();
  }
```
교체:
```javascript
  if (!key) {
    titleEl.textContent = "회의 키가 URL 에 없습니다.";
  } else if (getPw()) {
    connect();
  }
```

- [ ] **Step 4: 캡처/admin 스크립트 제거**

스크립트 IIFE 안의 **admin 인증 + mic 캡처 섹션 전체**(주석 `// ---- admin 인증 + mic 캡처 ----` 부터 mic-toggle click 핸들러까지: `ADMIN_KEY` 재선언, `adminToggle`/`adminPw`/`adminUnlock`/`micToggle`/`micLevel` getElementById, `showAdminControls`, `getPw`(중복), `downsample`/`floatToInt16`/`requestWakeLock`/`visibilitychange`/`loseMic`/`micStart`/`micStop`/mic-toggle 리스너)를 **삭제**한다. (mic-take 는 홈으로 이동했으므로 회의 페이지엔 불필요.)

CSS 의 `#admin-bar`, `.admin-hidden`, `#mic-level`, `#mic-bar` 관련 규칙은 남아도 무해하나, 정리하려면 삭제. `#mic-src` 스타일은 남긴다(배지).

주의: Step 3 에서 `ADMIN_KEY`/`getPw` 를 이미 선언했으니, Step 4 에서 admin 섹션의 **중복 선언을 제거**해 충돌(SyntaxError)을 막는다.

- [ ] **Step 5: 타입체크 + 무결성 확인 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `grep -c 'id="mic-toggle"\|admin-toggle\|micStart' jarvis-web/src/static/meeting.html` → 0 (캡처/admin 제거됨)
Run: `grep -c 'id="mic-src"\|id="login"\|case "mic_source"' jarvis-web/src/static/meeting.html` → 3 (배지·로그인·배지핸들러 존재)
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/meeting.html
git commit -m "refactor(jarvis-web): 회의 뷰 전용화 — admin-bar/캡처 제거, 로그인+repath"
```

---

## Task 5: 통합 검증 스크립트 + 배포 + .env 갱신

**Files:** `jarvis-web/scripts/mic_relay_check.mjs`, jarvis `.env`(미커밋)

- [ ] **Step 1: mic_relay_check.mjs 에 /subscribe 인증 검증 추가**

`jarvis-web/scripts/mic_relay_check.mjs` 의 `main()` 안, 기존 검증들 다음(`[recv, send, viewer, viewer2].forEach...` 직전)에 추가:
```javascript
  // 7) /subscribe 는 무토큰 거부, ADMIN_PASSWORD 로 통과
  let subRejected = false;
  try { await open(`${BASE}/subscribe/${KEY}`); } catch { subRejected = true; }
  console.log("subscribe 무토큰 거부:", subRejected ? "OK" : "FAIL");
  const sub = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  console.log("subscribe admin 통과:", "OK");
  sub.close();
```
(기존 `viewer`/`viewer2` 가 `/subscribe` 를 토큰 없이 여는 부분이 있으면, 그 `open` 호출들에도 `?token=${ADMIN}` 를 붙여 갱신한다. 안 그러면 이제 401 로 깨짐.)

- [ ] **Step 2: best-effort 라이브 검증**

`.dev.vars` 에 `RELAY_TOKEN=devtoken`, `ADMIN_PASSWORD=adminpw` 확인.
터미널1: `cd jarvis-web && npx wrangler dev --port 8787 &>/tmp/jw.log &` (8~10초 대기)
터미널2: `cd jarvis-web && RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs`
기대: 모든 줄 `OK` (subscribe 무토큰 거부/admin 통과 포함). 끝나면 `pkill -f wrangler`.
(이 환경에서 wrangler dev 가 안 뜨면 스킵하고 사유 보고.)

- [ ] **Step 3: 커밋(스크립트만)**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/scripts/mic_relay_check.mjs
git commit -m "test(jarvis-web): /subscribe 인증 검증 추가"
```

- [ ] **Step 4: 배포 + secret + .env (컨트롤러가 머지 후 수행 — 비코드)**

이 단계는 머지 후 컨트롤러(메인 세션)가 처리한다. 서브에이전트는 **건너뛴다**:
1. `cd jarvis-web && printf 'devconcode' | npx wrangler secret put ADMIN_PASSWORD`
2. `cd jarvis-web && printf '<기존 relay 토큰>' | npx wrangler secret put RELAY_TOKEN`
3. `cd jarvis-web && npm run deploy` → `https://jarvis-web.mjshin82.workers.dev`
4. jarvis `.env` 의 `RELAY_URL` 을 `wss://jarvis-web.mjshin82.workers.dev` 로 갱신.
5. 옛 worker 삭제: `npx wrangler delete --name meeting-web-jarvis` (확인 후).
6. 스모크: `curl .../healthz`, `curl .../Concode` 가 home HTML, `curl .../Concode/meeting` 가 meeting HTML, `/subscribe/Concode`(무토큰) 401.

---

## Self-Review 결과

**Spec coverage:**
- 리네임(dir+worker) → Task 1 ✓
- /{name}·/{name}/meeting 라우팅 + 우선순위 → Task 2 ✓
- /subscribe ADMIN 게이트 → Task 2 ✓
- 홈(로그인+mic-take+배지+nav+채팅 placeholder) → Task 3 ✓
- 회의 뷰 전용화(admin-bar 제거, 로그인, repath, 토큰) → Task 4 ✓
- 통합 검증 + 배포 + .env → Task 5 ✓
- 연기(채팅 대화/모드전환/DO 리네임) → 미구현 ✓

**Type/이름 consistency:** WS 평면 경로 유지(jarvis 무변경) · `ADMIN_KEY="jarvis_admin_pw"` 홈·회의 공통 · `requireAdmin`/`checkSecret` 재사용 · key 추출 = 첫 경로 세그먼트(홈·회의 동일 규칙) · 배지 kind `mic_source`/`source` 일치.

**핵심 위험:** (1) Hono 라우트 순서 — `/:name*` 를 구체 라우트 뒤에 등록(Task 2 Step1 명시). (2) meeting.html 에서 `ADMIN_KEY`/`getPw` 중복 선언 제거(Task 4 Step4 명시). (3) 기존 통합 스크립트의 무토큰 `/subscribe` 호출을 토큰 버전으로 갱신(Task 5 Step1).
