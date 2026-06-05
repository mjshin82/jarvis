# jarvis-web 단일 셸 SPA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 홈/회의를 한 셸(`app.html`)의 두 뷰로 합쳐, 음성 모드 전환(navigate) 시 **페이지 리로드 없이** 뷰만 전환 → mic-take/구독 WebSocket 이 끊기지 않는다.

**Architecture:** 워커가 `/:name` 과 `/:name/meeting` 양쪽에 동일한 `app.html` 셸을 서빙. 셸은 공용 상단바(로그인·마이크 토글·배지) + `#home-view`(채팅) + `#meeting-view`(자막) 두 뷰를 갖고, `navigate` 이벤트에서 `showView()` + `history.pushState()` 로 in-place 전환한다. 마이크 캡처와 구독 WS 는 셸 레벨에 살아 전환에도 유지된다.

**Tech Stack:** Cloudflare Worker(TS, Hono) · 바닐라 JS/HTML · wrangler `[[rules]] type="Text"` 로 `.html` 문자열 번들.

전제: `cd /Users/oracle/Documents/concode/jarvis`. typecheck: `cd jarvis-web && npm run typecheck`. 로컬 워커: `cd jarvis-web && npx wrangler dev --port 8787`.

---

## Task 1: app.html — 통합 셸(두 뷰)

**Files:** Create `jarvis-web/src/static/app.html`

기존 `home.html`(채팅+mic-take)과 `meeting.html`(자막)을 합친 단일 셸. 아래 **전체 내용**을 그대로 파일로 만든다. (HTML/인라인 JS 라 단위 테스트 대신 구조 grep + 이후 Task 의 typecheck/라우트 체크/수동 E2E 로 검증한다.)

- [ ] **Step 1: 파일 생성** — `jarvis-web/src/static/app.html` 을 아래 내용 그대로 작성:

```html
<!-- jarvis-web/src/static/app.html — 홈+회의 통합 셸(SPA) -->
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover" />
<title>Jarvis</title>
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
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", "Noto Sans KR", Arial, sans-serif; }
  body { display: flex; flex-direction: column; }
  body[data-view="home"] { height: 100vh; }
  body[data-view="meeting"] { min-height: 100vh; }

  header {
    position: sticky; top: 0; z-index: 10;
    padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--bg);
    display: flex; align-items: center; gap: 12px;
  }
  header .title { font-size: 18px; font-weight: 600; }
  #meta-badge { font-size: 12px; color: var(--muted); }
  #mic-src { padding: 3px 8px; border-radius: 6px; background: #8882; font-size: 13px; }
  #mic-src.remote { background: #22c55e; color: #03240f; }
  #conn { margin-left: auto; font-size: 13px; opacity: 0.8; }
  #conn.ok { color: var(--ko); opacity: 1; }
  #conn.bad { color: #d33; opacity: 1; }

  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
    padding: 10px 14px; border-bottom: 1px solid var(--border); }
  button { font-size: 15px; padding: 8px 16px; border-radius: 10px; border: none; background: var(--accent); color: #fff; cursor: pointer; }
  button.off { background: #dc2626; }
  #mic-level { width: 120px; height: 8px; background: #ddd4; border-radius: 4px; overflow: hidden; }
  #mic-bar { height: 100%; width: 0%; background: #22c55e; }

  /* 뷰 토글 */
  #home-view, #meeting-view { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  body[data-view="home"] #meeting-view { display: none; }
  body[data-view="meeting"] #home-view { display: none; }

  /* 홈 채팅 */
  #chat { flex: 1; padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
  .bubble { max-width: 80%; padding: 8px 12px; border-radius: 14px; white-space: pre-wrap; line-height: 1.4; }
  .bubble.user { align-self: flex-end; background: var(--accent); color: #fff; }
  .bubble.assistant { align-self: flex-start; background: #8883; }

  /* 회의 자막 */
  #log { flex: 1; padding: 16px; max-width: 980px; margin: 0 auto; width: 100%; }
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
  .lockbar { position: fixed; bottom: 48px; left: 50%; transform: translateX(-50%);
    background: var(--accent); color: white; padding: 6px 14px; border-radius: 999px;
    font-size: 13px; cursor: pointer; opacity: 0; pointer-events: none; transition: opacity .2s; }
  .lockbar.show { opacity: 1; pointer-events: auto; }
  @media (max-width: 600px) { .src { font-size: 17px; } .tx { font-size: 16px; } }

  #login { position: fixed; inset: 0; background: #000a; display: flex; flex-direction: column;
    gap: 12px; align-items: center; justify-content: center; z-index: 20; }
  #login input { font-size: 16px; padding: 10px; width: 240px; border-radius: 8px; border: 1px solid #888; }
  .hidden { display: none !important; }
</style>
</head>
<body data-view="home">
  <div id="login">
    <div>🔒 Jarvis 로그인</div>
    <input id="login-pw" type="password" placeholder="password" />
    <button id="login-go">들어가기</button>
  </div>

  <header>
    <span class="title" id="title">🤖 Jarvis</span>
    <span id="meta-badge"></span>
    <span id="mic-src"></span>
    <span id="conn">···</span>
  </header>
  <div id="controls">
    <button id="mic-toggle">🎙️ 마이크 켜기</button>
    <div id="mic-level" class="hidden"><div id="mic-bar"></div></div>
  </div>

  <div id="home-view">
    <main id="chat"></main>
  </div>
  <div id="meeting-view">
    <main id="log"></main>
    <div class="lockbar" id="lockbar">↓ 새 자막 보기</div>
  </div>

<script>
(() => {
  const ADMIN_KEY = "jarvis_admin_pw";
  const TARGET_SR = 16000;
  const name = decodeURIComponent(location.pathname.replace(/^\//, "").replace(/\/.*$/, "")) || "jarvis";
  const $ = (id) => document.getElementById(id);

  // ---- 로그인 ----
  function getPw() { return localStorage.getItem(ADMIN_KEY) || ""; }
  function showLogin() { $("login").classList.remove("hidden"); }
  function hideLogin() { $("login").classList.add("hidden"); }
  $("login-go").addEventListener("click", () => {
    ensureAudio();
    const pw = $("login-pw").value.trim();
    if (!pw) return;
    localStorage.setItem(ADMIN_KEY, pw);
    hideLogin();
    connect();
  });

  // ---- 뷰 전환 (리로드 없음) ----
  function pathIsMeeting() { return /\/meeting\/?$/.test(location.pathname); }
  function setView(v) { document.body.dataset.view = (v === "meeting" ? "meeting" : "home"); }
  function showView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    if (document.body.dataset.view === nv) return;
    setView(nv);
    const path = nv === "meeting" ? `/${encodeURIComponent(name)}/meeting` : `/${encodeURIComponent(name)}`;
    history.pushState({ view: nv }, "", path);
  }
  window.addEventListener("popstate", () => setView(pathIsMeeting() ? "meeting" : "home"));

  // ---- TTS Audio + 채팅 버블 (홈) ----
  let audioCtx = null, playHead = 0;
  function ensureAudio() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  }
  function playAudio(buf) {
    ensureAudio();
    const sr = new DataView(buf).getUint32(0, true);
    const pcm = new Int16Array(buf, 4);
    const f32 = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;
    const ab = audioCtx.createBuffer(1, f32.length, sr);
    ab.copyToChannel(f32, 0);
    const s = audioCtx.createBufferSource();
    s.buffer = ab; s.connect(audioCtx.destination);
    const t = Math.max(audioCtx.currentTime, playHead);
    s.start(t); playHead = t + ab.duration;
  }
  let lastRole = null, lastBubble = null;
  function addText(role, text) {
    const chat = $("chat");
    if (role === lastRole && lastBubble) { lastBubble.textContent += " " + text; }
    else {
      const b = document.createElement("div");
      b.className = "bubble " + role; b.textContent = text;
      chat.appendChild(b); lastRole = role; lastBubble = b;
    }
    chat.scrollTop = chat.scrollHeight;
  }

  // ---- 회의 자막 (meeting) ----
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
    const partner = meta.partner || "?", user = meta.user || "?";
    $("title").textContent = `🎤 ${partner} ↔ ${user}`;
    const tags = [];
    if (meta.partner_lang) tags.push(`${partner}: ${meta.partner_lang}`);
    if (meta.user_lang) tags.push(`${user}: ${meta.user_lang}`);
    $("meta-badge").textContent = tags.join(" · ");
  }
  function newCard() {
    const card = document.createElement("section");
    card.className = "card";
    $("log").appendChild(card);
    return card;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  // ---- 이벤트 디스패치 (홈+회의 통합) ----
  function handle(ev) {
    switch (ev.kind) {
      case "user": addText("user", ev.text || ""); return;
      case "assistant": addText("assistant", ev.text || ""); return;
      case "navigate": showView(ev.text); return;
      case "hello": applyMeta(ev.meta); return;
      case "info": {
        const card = newCard();
        card.innerHTML = `<div class="info">${escapeHtml(ev.text || "")}</div>`;
        lastCard = null;
        break;
      }
      case "source": {
        let card;
        if (draftCard) {
          card = draftCard; card.classList.remove("draft");
          card.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`;
          draftCard = null;
        } else {
          card = newCard();
          card.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`;
        }
        lastCard = card;
        break;
      }
      case "translation_ko":
      case "translation_en": {
        const cls = ev.kind === "translation_ko" ? "ko" : "en";
        const sym = ev.kind === "translation_ko" ? "🌐" : "🇺🇸";
        const html = `<div class="tx ${cls}">${sym} ${escapeHtml(ev.text || "")}</div>`;
        if (lastCard) { lastCard.insertAdjacentHTML("beforeend", html); }
        else { const card = newCard(); card.innerHTML = html; lastCard = card; }
        break;
      }
      case "partial": {
        if (!draftCard) { draftCard = newCard(); draftCard.classList.add("draft"); }
        draftCard.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`;
        scrollIfLocked();
        return;
      }
      case "gap":
        lastCard = null;
        if (draftCard) { draftCard.remove(); draftCard = null; }
        return;
      case "end": {
        const card = newCard();
        card.innerHTML = `<div class="info">— 회의 종료 —</div>`;
        $("conn").textContent = "🛑 publisher ended";
        $("conn").classList.remove("ok"); $("conn").classList.add("bad");
        break;
      }
      case "kicked": {
        const card = newCard();
        card.innerHTML = `<div class="info">— 새 publisher 가 채널을 인수했습니다 —</div>`;
        break;
      }
      case "publisher_disconnected": {
        $("conn").textContent = "⏸ publisher 연결 끊김";
        $("conn").classList.remove("ok"); $("conn").classList.add("bad");
        return;
      }
      case "mic_source": {
        $("mic-src").textContent = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
        $("mic-src").classList.toggle("remote", ev.source === "remote");
        break;
      }
      default: return;
    }
    scrollIfLocked();
  }

  // ---- 구독 WS (홈+회의 공용) ----
  let ws = null, reconnectDelay = 500;
  function connect() {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/subscribe/${encodeURIComponent(name)}?token=${encodeURIComponent(pw)}`);
    ws.binaryType = "arraybuffer";
    ws.addEventListener("open", () => {
      $("conn").textContent = "● live";
      $("conn").classList.remove("bad"); $("conn").classList.add("ok");
      reconnectDelay = 500;
    });
    ws.addEventListener("message", (m) => {
      if (m.data instanceof ArrayBuffer) { playAudio(m.data); return; }
      try { handle(JSON.parse(m.data)); } catch (e) { console.error("bad message", e, m.data); }
    });
    ws.addEventListener("close", (e) => {
      if (e.code === 1006 || e.code === 1008) {
        localStorage.removeItem(ADMIN_KEY);
        $("conn").textContent = "인증 필요"; $("conn").classList.add("bad");
        showLogin();
        return;
      }
      $("conn").textContent = `재연결 (${reconnectDelay/1000}s)…`;
      $("conn").classList.remove("ok"); $("conn").classList.add("bad");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 8000);
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch {} });
  }

  // ---- mic-take (셸 레벨 — 뷰 전환에도 유지) ----
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
    ensureAudio();
    micOn = !micOn;
    $("mic-toggle").textContent = micOn ? "🎙️ 마이크 끄기" : "🎙️ 마이크 켜기";
    $("mic-toggle").classList.toggle("off", micOn);
    if (micOn) { try { await micStart(); } catch (e) { alert("마이크 권한 실패: " + e.message); micOn = false; $("mic-toggle").textContent = "🎙️ 마이크 켜기"; $("mic-toggle").classList.remove("off"); } }
    else micStop();
  });

  // ---- 초기화 ----
  setView(pathIsMeeting() ? "meeting" : "home");
  if (getPw()) { hideLogin(); connect(); } else { showLogin(); }
})();
</script>
</body>
</html>
```

- [ ] **Step 2: 구조 검증** — 핵심 마커가 모두 있는지 확인:

Run:
```bash
cd /Users/oracle/Documents/concode/jarvis
grep -c 'id="home-view"\|id="meeting-view"\|function showView\|history.pushState\|async function micStart\|function addText\|function handle(ev)\|function playAudio\|data-view' jarvis-web/src/static/app.html
```
Expected: `9` (마커 9종 각 1회 이상 — 총 매칭 줄 수 9).

- [ ] **Step 3: 커밋**
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(jarvis-web): app.html — 홈+회의 통합 셸(SPA, 뷰 전환)"
```

---

## Task 2: index.ts — 두 라우트가 app.html 서빙 + 옛 파일 삭제

**Files:** Modify `jarvis-web/src/index.ts`, Delete `jarvis-web/src/static/home.html`, `jarvis-web/src/static/meeting.html`

현재 `index.ts` 18-19행:
```ts
import MEETING_HTML from "./static/meeting.html";
import HOME_HTML    from "./static/home.html";
```
현재 HTML 핸들러(66-76행):
```ts
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

- [ ] **Step 1: import 교체** — 18-19행의 두 import:
```ts
import MEETING_HTML from "./static/meeting.html";
import HOME_HTML from "./static/home.html";
```
를 한 줄로 교체:
```ts
import APP_HTML from "./static/app.html";
```

- [ ] **Step 2: 두 핸들러가 APP_HTML 반환** — 위 두 핸들러를 아래로 교체(헤더·상태 그대로, 본문 상수만 `APP_HTML`). 두 라우트 정의는 유지(직접 링크/북마크 보존):
```ts
app.get("/:name/meeting", (c) => {
  return new Response(APP_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});

app.get("/:name", (c) => {
  return new Response(APP_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```

- [ ] **Step 3: 옛 정적 파일 삭제**
```bash
cd /Users/oracle/Documents/concode/jarvis
git rm jarvis-web/src/static/home.html jarvis-web/src/static/meeting.html
```

- [ ] **Step 4: 검증**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `cd /Users/oracle/Documents/concode/jarvis && grep -c 'HOME_HTML\|MEETING_HTML' jarvis-web/src/index.ts` → `0`
Run: `grep -c 'APP_HTML' jarvis-web/src/index.ts` → `3` (import 1 + 라우트 2)
Run: `test -f jarvis-web/src/static/home.html && echo EXISTS || echo GONE` → `GONE`; 동일하게 meeting.html → `GONE`

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/index.ts jarvis-web/src/static/home.html jarvis-web/src/static/meeting.html
git commit -m "feat(jarvis-web): 두 라우트가 통합 셸(app.html) 서빙 + 옛 페이지 삭제"
```

---

## Task 3: 동일 셸 서빙 검증 + 배포

**Files:** Modify `jarvis-web/scripts/mic_relay_check.mjs`

`/:name` 과 `/:name/meeting` 가 **동일한 셸 HTML**(200 + text/html)을 반환하는지 자동 검증(HTTP fetch). Node 18+ 의 전역 `fetch` 사용.

- [ ] **Step 1: 라우트 검증 추가** — `mic_relay_check.mjs` 의 `main()`, 마지막 정리(`[recv, send, ...].forEach(...)`) **직전**에 추가:
```javascript
  // 11) /:name 과 /:name/meeting 가 동일 셸 HTML 반환
  const httpBase = BASE.replace(/^ws/, "http");
  const r1 = await fetch(`${httpBase}/Concode`);
  const r2 = await fetch(`${httpBase}/Concode/meeting`);
  const t1 = await r1.text(), t2 = await r2.text();
  const okShell = r1.status === 200 && r2.status === 200
    && (r1.headers.get("content-type") || "").includes("text/html")
    && t1 === t2 && t1.includes("data-view") && t1.includes('id="meeting-view"');
  console.log("동일 셸 서빙:", okShell ? "OK" : `FAIL (s1=${r1.status} s2=${r2.status} eq=${t1 === t2})`);
```

- [ ] **Step 2: best-effort 라이브 런** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s 부팅 대기, `/tmp/jw.log` 에서 "Ready" 확인) → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 모든 줄 OK 기대(신규 "동일 셸 서빙: OK" 포함) → `pkill -f "wrangler dev"`. wrangler 가 안 뜨면 스킵 + 사유 기록(편집은 그대로). `node --check scripts/mic_relay_check.mjs` 로 구문은 항상 검증.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/scripts/mic_relay_check.mjs
git commit -m "test(jarvis-web): 두 라우트 동일 셸 서빙 검증"
```

- [ ] **Step 4: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 폰 홈 → mic-take → "미팅 모드로 변경해줘" → **리로드 없이** 회의 뷰 + URL `/Concode/meeting` + 마이크 유지(jarvis 가 계속 remote 소스) → "회의 끝내줘" → 홈 뷰 복귀 + 마이크 유지. 뒤로가기로도 뷰 전환 확인.

---

## Self-Review 결과

**Spec coverage:**
- 단일 셸 `app.html`(공용 상단바 + home-view/meeting-view) → Task 1 ✓
- 마이크/구독 WS 셸 레벨 유지, navigate→showView+pushState, popstate → Task 1 ✓
- `name`/`key` 통일, 로그인 `.hidden` 통일, connect 1벌(바이너리 분기), escapeHtml 유지, conn ok/bad → Task 1 ✓
- 워커 두 라우트 APP_HTML 서빙, 옛 파일 삭제, `[[rules]]` 그대로 → Task 2 ✓
- 동일 셸 서빙 자동 검증 + 수동 E2E + 배포 → Task 3 ✓
- 비범위(북마크 직접 진입 자동 마이크 재개) → 미구현 ✓

**Placeholder scan:** Task 1 은 완전한 파일 본문 포함. Task 2 는 기존 `c.html` 호출형태 보존 지시(실파일 기준) — 본문 상수만 교체로 구체적. 빈칸 없음.

**Type/이름 consistency:** `name`(경로 첫 세그먼트, 두 경로 동일 추출) · `showView("meeting"|"home")` ↔ `navigate` ev.text ↔ `setView`/`pathIsMeeting` · `APP_HTML`(import 1 + 라우트 2 = grep 3) · 마커 grep 9. handle() 의 case 들이 기존 두 파일의 kind 와 일치(user/assistant/navigate/hello/info/source/translation_*/partial/gap/end/kicked/publisher_disconnected/mic_source).

**핵심 위험:** (1) home 은 `#chat` 내부 스크롤(body 100vh), meeting 은 페이지 스크롤(body min-height) — `body[data-view]` 로 분기. 레이아웃은 수동 E2E 로 최종 확인. (2) navigate 는 직전 커밋에서 replay 버퍼 제외됨 → 새 viewer stale 전환 없음. (3) 마이크가 셸 레벨이라 showView 가 micWS/micStream 을 건드리지 않음 — 연속성 보장.
