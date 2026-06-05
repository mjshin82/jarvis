# 웹 admin 인증 + mic 토글/소스 표시 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** meeting-web 자막 뷰어에 `ADMIN_PASSWORD` 기반 admin 인증과 mic 토글 버튼을 넣고, jarvis 가 실제로 듣는 마이크 소스(시스템/원격)를 뷰어에 실시간 표시한다.

**Architecture:** 인증을 `RELAY_TOKEN`(백엔드)과 `ADMIN_PASSWORD`(사람)로 분리한다. 캡처 UI 를 별도 페이지에서 자막 뷰어(`/m/:key`)로 흡수한다. jarvis 의 `MicRouter` 가 소스 전환 시 `on_switch` 훅으로 `RemoteMicReceiver` 에 알리고, receiver 가 기존 `/mic-recv` 소켓을 양방향으로 써서 `mic_source` 상태를 relay 로 올리면 DO 가 viewer 들에게 broadcast 한다.

**Tech Stack:** Python 3.11 (asyncio, websockets, pytest) · Cloudflare Workers (Hono + Durable Objects, TypeScript) · 바닐라 JS.

---

## 파일 구조

| 파일 | 변경 |
|------|------|
| `mic_source.py` | `MicRouter.on_switch` 훅 (전환 시 콜백) |
| `remote_mic_receiver.py` | 양방향(동시 recv+send), `notify_source`, 재연결 시 상태 재송신 |
| `main.py` | `on_switch`→`notify_source` 배선, 시작 URL 박스를 `/m/<key>`로 |
| `meeting-web/src/types.ts` | `mic_source` kind + `source` 필드 |
| `meeting-web/src/index.ts` | `requireAdmin`/`requireRelayToken` 분리, `/capture` 제거, `Env.ADMIN_PASSWORD` |
| `meeting-web/src/meeting_do.ts` | `lastMicSource`, receiver 메시지 핸들러→broadcast, viewer 1회 전송 |
| `meeting-web/src/static/meeting.html` | admin 잠금 + mic 토글 + 소스 배지 + 캡처 로직 이식 |
| `meeting-web/src/static/capture.html` | **삭제** |
| `meeting-web/scripts/mic_relay_check.mjs` | mic_source 흐름 + admin 인증 검증 추가 |

전제: 항상 `cd /Users/oracle/Documents/concode/jarvis`. pytest 는 `.venv/bin/python -m pytest`. meeting-web 검증은 `cd meeting-web && npm run typecheck`.

---

## Task 1: MicRouter.on_switch 훅

소스가 실제로 바뀔 때 콜백을 호출한다. 콜백은 main 에서 receiver 생성 후 주입한다(순환 의존 회피).

**Files:**
- Modify: `mic_source.py` (`MicRouter`)
- Test: `tests/test_mic_router.py` (추가)

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_mic_router.py` 끝에 추가:
```python
def test_on_switch_called_with_new_source():
    q = queue.Queue()
    seen = []
    r = MicRouter(q, local=_FakeLocal(), remote=_FakeRemote())
    r.on_switch = seen.append
    r.note_remote_activity(now=1.0)        # local→remote
    r.set_override("local")                # remote→local
    r.set_override("local")                # 변화 없음 → 콜백 없음
    assert seen == ["remote", "local"]
```
(`_FakeLocal`/`_FakeRemote` 는 이미 이 파일에 있다 — `_FakeLocal` 은 start/stop, `_FakeRemote` 는 reset.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_mic_router.py::test_on_switch_called_with_new_source -v`
Expected: FAIL — `AttributeError: 'MicRouter' object has no attribute 'on_switch'` (또는 콜백 미호출)

- [ ] **Step 3: 구현**

`MicRouter.__init__` 의 시그니처와 본문을 수정 — `on_switch` 파라미터 추가:
```python
    def __init__(self, block_queue, *, local=None, remote=None, clock=time.monotonic, on_switch=None):
        self._q = block_queue
        self._clock = clock
        self._mode = "auto"
        self._active = "local"
        self._last_remote = 0.0
        self._suppressed = False   # 회의 모드 등에서 원격 프레임 처리 일시 중단
        self.on_switch = on_switch   # 소스 전환 시 호출(source: str). 나중에 주입 가능.
        self.local = local if local is not None else LocalMicSource(sink=self._sink_local)
        self.remote = remote if remote is not None else RemoteMicSource(sink=self._sink_remote)
```

`_switch` 의 끝(잔여 드레인 + `self.remote.reset()` 이후)에 콜백 호출을 추가. 현재 `_switch` 는:
```python
    def _switch(self, target):
        if self._active == target:
            return
        self._active = target
        # 소스 간 오디오 혼입 방지: 큐 잔여 비우고 원격 재청크 버퍼 리셋
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self.remote.reset()
```
맨 끝(`self.remote.reset()` 다음 줄)에 추가:
```python
        if self.on_switch is not None:
            self.on_switch(self._active)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_mic_router.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add mic_source.py tests/test_mic_router.py
git commit -m "feat: MicRouter.on_switch — 소스 전환 훅"
```

---

## Task 2: RemoteMicReceiver 양방향 + notify_source

`/mic-recv` 소켓을 recv 와 동시에 send 가능하게 하고, 소스 상태를 relay 로 올린다.

**Files:**
- Modify: `remote_mic_receiver.py`
- Test: `tests/test_remote_mic_receiver.py` (추가)

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_remote_mic_receiver.py` 끝에 추가:
```python
def test_notify_source_caches_and_enqueues():
    rx = RemoteMicReceiver("ws://x", "tok", FakeRouter(), on_log=lambda *_: None)
    rx.notify_source("remote")
    assert rx._last_source == "remote"
    msg = rx._outbound.get_nowait()
    assert msg == {"kind": "mic_source", "source": "remote"}


def test_send_loop_writes_queued_to_ws():
    rx = RemoteMicReceiver("ws://x", "tok", FakeRouter(), on_log=lambda *_: None)

    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send(self, data):
            self.sent.append(data)

    async def main():
        ws = FakeWS()
        rx.notify_source("system")
        task = asyncio.create_task(rx._send_loop(ws))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ws.sent

    sent = asyncio.run(main())
    assert any('"kind": "mic_source"' in s and '"source": "system"' in s for s in sent)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_remote_mic_receiver.py -v`
Expected: FAIL — `AttributeError: 'RemoteMicReceiver' object has no attribute 'notify_source'`

- [ ] **Step 3: 구현**

`remote_mic_receiver.py` 의 `__init__` 에 outbound 큐와 last_source 캐시를 추가. 현재 `__init__` 끝부분(`self._stop = asyncio.Event()` / `self._task = None` 근처)에 추가:
```python
        self._outbound: asyncio.Queue = asyncio.Queue()
        self._last_source = None
```

`notify_source` 메서드 추가(클래스 내, `_handle_message` 근처):
```python
    def notify_source(self, source) -> None:
        """MicRouter.on_switch 로 연결 — 소스 상태를 relay 로 올린다(동기, 큐 적재)."""
        self._last_source = source
        try:
            self._outbound.put_nowait({"kind": "mic_source", "source": source})
        except asyncio.QueueFull:
            pass
```

`_send_loop` 메서드 추가:
```python
    async def _send_loop(self, ws) -> None:
        while True:
            msg = await self._outbound.get()
            await ws.send(json.dumps(msg))
```

`_recv_loop` 메서드 추가:
```python
    async def _recv_loop(self, ws) -> None:
        async for message in ws:
            await self._handle_message(message)
```

`_connect_once` 를 recv+send 동시 실행으로 교체. 현재:
```python
    async def _connect_once(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self.on_log("[mic] 원격 마이크 수신 대기 중")
            async for message in ws:
                await self._handle_message(message)
```
로 바꿔서:
```python
    async def _connect_once(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self.on_log("[mic] 원격 마이크 수신 대기 중")
            # (re)연결 직후 현재 소스 1회 동기화 (끊김 사이 전환 복구)
            if self._last_source is not None:
                await ws.send(json.dumps({"kind": "mic_source", "source": self._last_source}))
            recv = asyncio.create_task(self._recv_loop(ws))
            send = asyncio.create_task(self._send_loop(ws))
            done, pending = await asyncio.wait({recv, send}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc
```

(`json` 은 이미 import 되어 있다.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_remote_mic_receiver.py -v`
Expected: PASS (기존 2 + 신규 2 = 4). 그리고 `.venv/bin/python -m pytest tests/ -q` → 0 failed.

- [ ] **Step 5: 커밋**

```bash
git add remote_mic_receiver.py tests/test_remote_mic_receiver.py
git commit -m "feat: RemoteMicReceiver 양방향 — notify_source 로 소스 상태 송신"
```

---

## Task 3: main.py 배선 + URL 박스

`on_switch`→`notify_source` 연결, 시작 URL 박스를 통합 뷰어 `/m/<key>`로.

**Files:**
- Modify: `main.py`

테스트가 어려운 배선이라 import 스모크 + 전체 suite 로 검증한다.

- [ ] **Step 1: on_switch 배선**

`main.py` 에서 `remote_mic_rx.start()` 호출 직후 줄에 추가:
```python
        mic.router.on_switch = remote_mic_rx.notify_source
```
(현재 `remote_mic_rx.start()` 다음 줄이 `remote_mic_monitor = asyncio.create_task(...)` 이다 — 그 사이에 넣는다.)

- [ ] **Step 2: URL 박스를 /m/<key> 로**

현재 박스 블록:
```python
        cap_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
        cap_url = f"{cap_base}/capture/{config.REMOTE_MIC_KEY}"
        box_width = max(len(cap_url) + 4, 60)
        border = "─" * box_width
        console.log("")
        console.log(f"┌{border}┐")
        console.log(f"│  📱 원격 마이크 (이 URL 을 폰/타블렛에서 열기)".ljust(box_width + 1) + "│")
        console.log(f"│  {cap_url}".ljust(box_width + 1) + "│")
        console.log(f"└{border}┘")
        console.log("")
```
를 다음으로 교체:
```python
        cap_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
        cap_url = f"{cap_base}/m/{config.REMOTE_MIC_KEY}"
        box_width = max(len(cap_url) + 4, 60)
        border = "─" * box_width
        console.log("")
        console.log(f"┌{border}┐")
        console.log(f"│  📱 회의/원격마이크 페이지 (admin 로그인 후 mic 토글)".ljust(box_width + 1) + "│")
        console.log(f"│  {cap_url}".ljust(box_width + 1) + "│")
        console.log(f"└{border}┘")
        console.log("")
```

- [ ] **Step 3: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); print('parse ok')"`
Expected: `parse ok`
Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 0 failed

- [ ] **Step 4: 커밋**

```bash
git add main.py
git commit -m "feat: main — on_switch→notify_source 배선, URL 박스를 /m 으로"
```

---

## Task 4: meeting-web types.ts — mic_source

**Files:**
- Modify: `meeting-web/src/types.ts`

- [ ] **Step 1: EventKind 에 추가**

`EventKind` union 의 `"no_receiver";` 를 다음으로 교체:
```typescript
  | "no_receiver"
  | "mic_source";          // jarvis 가 듣는 소스 상태 (system|remote)
```

- [ ] **Step 2: source 필드 추가**

`ClientMessage` 인터페이스에 `source` 를 추가:
```typescript
export interface ClientMessage {
  kind: EventKind;
  text?: string;
  meta?: MeetingMeta;
  reason?: string;
  source?: "system" | "remote";
}
```
(`RelayEvent extends ClientMessage` 이므로 자동 상속.)

- [ ] **Step 3: 타입체크 + 커밋**

Run: `cd meeting-web && npm run typecheck` → 오류 없음
```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/src/types.ts
git commit -m "feat(meeting-web): mic_source 이벤트 타입 + source 필드"
```

---

## Task 5: meeting-web index.ts — 인증 분리 + /capture 제거

`/mic` 은 ADMIN_PASSWORD, `/mic-recv` 는 RELAY_TOKEN. 캡처 페이지/라우트 제거.

**Files:**
- Modify: `meeting-web/src/index.ts`
- Delete: `meeting-web/src/static/capture.html`

- [ ] **Step 1: Env 에 ADMIN_PASSWORD 추가**

```typescript
interface Env {
  RELAY_TOKEN: string;
  ADMIN_PASSWORD: string;
  MEETING_DO: DurableObjectNamespace;
}
```

- [ ] **Step 2: CAPTURE_HTML import + /capture 라우트 제거**

`import CAPTURE_HTML from "./static/capture.html";` 줄을 삭제.
다음 라우트 블록 전체를 삭제:
```typescript
app.get("/capture/:key", (c) => {
  return new Response(CAPTURE_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});
```

- [ ] **Step 3: requireToken 을 둘로 분리**

기존 `requireToken` 함수를 다음 두 함수로 교체:
```typescript
function requireRelayToken(c: any): boolean {
  return checkSecret(c, c.env.RELAY_TOKEN);
}

function requireAdmin(c: any): boolean {
  return checkSecret(c, c.env.ADMIN_PASSWORD);
}

function checkSecret(c: any, expected: string): boolean {
  const auth = c.req.header("Authorization") || "";
  const headerTok = auth.replace(/^Bearer\s+/i, "").trim();
  const queryTok = (c.req.query("token") || "").trim();
  const tok = headerTok || queryTok;
  return !!tok && !!expected && tok === expected;
}
```

- [ ] **Step 4: /mic, /mic-recv 가드 교체**

`/mic/:key` 핸들러의 `if (!requireToken(c))` 를 `if (!requireAdmin(c))` 로.
`/mic-recv/:key` 핸들러의 `if (!requireToken(c))` 를 `if (!requireRelayToken(c))` 로.

- [ ] **Step 5: 라우트 주석 갱신**

상단 주석 블록에서 `/capture` 줄을 삭제하고 `/mic` 설명을 갱신:
```
 *   GET  /mic/:key            WebSocket: 마이크 송신 (ADMIN_PASSWORD 필요)
 *   GET  /mic-recv/:key       WebSocket: 마이크 수신=jarvis (RELAY_TOKEN 필요)
```

- [ ] **Step 6: capture.html 삭제**

```bash
git rm meeting-web/src/static/capture.html
```

- [ ] **Step 7: 타입체크 + 커밋**

Run: `cd meeting-web && npm run typecheck` → 오류 없음
```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/src/index.ts
git commit -m "feat(meeting-web): admin/relay 인증 분리, /capture 제거"
```

---

## Task 6: meeting-web meeting_do.ts — 소스 상태 broadcast

receiver(jarvis)가 보낸 `mic_source` 를 받아 viewer 들에게 broadcast + 신규 viewer 에 1회 전송.

**Files:**
- Modify: `meeting-web/src/meeting_do.ts`

- [ ] **Step 1: lastMicSource 필드 추가**

`micSender`/`micReceiver`/`lastNoReceiverAt` 필드 근처에 추가:
```typescript
  private lastMicSource: string | null = null;
```

- [ ] **Step 2: attachMicReceiver 에 메시지 핸들러 추가**

현재 `attachMicReceiver` 는 close/error 만 단다. message 핸들러를 추가 — `this.micReceiver = ws;` 다음 줄에 삽입:
```typescript
    ws.addEventListener("message", (msg) => {
      let parsed: any = null;
      try {
        const d = (msg as MessageEvent).data;
        const raw = typeof d === "string" ? d : new TextDecoder().decode(d as ArrayBuffer);
        parsed = JSON.parse(raw);
      } catch { return; }
      if (parsed && parsed.kind === "mic_source") {
        this.lastMicSource = parsed.source ?? null;
        this.broadcast(this.buildEvent({ kind: "mic_source", source: parsed.source }));
      }
    });
```

- [ ] **Step 3: attachViewer 에서 현재 소스 1회 전송**

`attachViewer` 의 replay 루프(`for (const ev of this.events) { this.safeSend(ws, ev); }`) 바로 다음에 추가:
```typescript
    if (this.lastMicSource) {
      this.safeSend(ws, this.buildEvent({ kind: "mic_source", source: this.lastMicSource as "system" | "remote" }));
    }
```

- [ ] **Step 4: 타입체크 + 커밋**

Run: `cd meeting-web && npm run typecheck` → 오류 없음
```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/src/meeting_do.ts
git commit -m "feat(meeting-web): mic_source 를 viewer 에 broadcast + 신규 viewer 동기화"
```

---

## Task 7: meeting-web meeting.html — admin 인증 + mic 토글 + 소스 배지

자막 뷰어에 admin 컨트롤을 통합한다. `capture.html` 의 캡처 로직을 이식한다.

**Files:**
- Modify: `meeting-web/src/static/meeting.html`

먼저 `meeting-web/src/static/meeting.html` 전체를 읽어 구조를 파악한다(헤더, `<style>`, IIFE `(() => { ... })()` 안의 `handle(ev)` switch, `connect()` 함수). 아래 조각들을 지정 위치에 넣는다.

- [ ] **Step 1: CSS 추가**

`<style>` 블록 끝(닫는 `</style>` 직전)에 추가:
```css
  #admin-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
               padding: 6px 12px; border-bottom: 1px solid #8883; font-size: 13px; }
  #admin-bar button { font-size: 13px; padding: 6px 12px; border-radius: 8px; border: none;
                      background: #2563eb; color: #fff; cursor: pointer; }
  #admin-bar button.off { background: #dc2626; }
  #admin-bar input { font-size: 13px; padding: 6px; width: 160px; }
  #mic-src { padding: 3px 8px; border-radius: 6px; background: #8882; }
  #mic-src.remote { background: #22c55e; color: #03240f; }
  #mic-level { width: 120px; height: 8px; background: #ddd4; border-radius: 4px; overflow: hidden; }
  #mic-bar { height: 100%; width: 0%; background: #22c55e; }
  .admin-hidden { display: none !important; }
```

- [ ] **Step 2: HTML 추가**

`<header>...</header>` 바로 다음(`<main id="log">` 앞)에 admin 바를 추가:
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

- [ ] **Step 3: handle() 에 mic_source 케이스 추가**

`handle(ev)` 의 switch 문에 케이스를 추가(예: `case "publisher_disconnected":` 근처):
```javascript
      case "mic_source": {
        const src = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
        const badge = document.getElementById("mic-src");
        badge.textContent = src;
        badge.classList.toggle("remote", ev.source === "remote");
        break;
      }
```

- [ ] **Step 4: admin/캡처 JS 추가**

IIFE `(() => { ... })()` 안, `connect();` 호출 직전(파일 끝의 `if (!key) {...} else { connect(); }` 블록 앞)에 다음을 통째로 추가:
```javascript
  // ---- admin 인증 + mic 캡처 ----
  const ADMIN_KEY = "jarvis_admin_pw";
  const TARGET_SR = 16000;
  let micWS = null, micCtx = null, micNode = null, micStream = null, micOn = false;

  const adminToggle = document.getElementById("admin-toggle");
  const adminPw = document.getElementById("admin-pw");
  const adminUnlock = document.getElementById("admin-unlock");
  const micToggle = document.getElementById("mic-toggle");
  const micLevel = document.getElementById("mic-level");

  function showAdminControls() {
    micToggle.classList.remove("admin-hidden");
    adminPw.classList.add("admin-hidden");
    adminUnlock.classList.add("admin-hidden");
    adminToggle.textContent = "🔓 admin";
  }
  function getPw() { return localStorage.getItem(ADMIN_KEY) || ""; }

  adminToggle.addEventListener("click", () => {
    if (getPw()) { showAdminControls(); return; }   // 이미 해제됨
    adminPw.classList.toggle("admin-hidden");
    adminUnlock.classList.toggle("admin-hidden");
  });
  adminUnlock.addEventListener("click", () => {
    const pw = adminPw.value.trim();
    if (!pw) return;
    localStorage.setItem(ADMIN_KEY, pw);
    showAdminControls();
  });
  if (getPw()) showAdminControls();   // 재방문 자동 해제

  function downsample(input, inRate) {
    if (inRate === TARGET_SR) return input;
    const ratio = inRate / TARGET_SR;
    const outLen = Math.floor(input.length / ratio);
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

  async function micStart() {
    const pw = getPw();
    if (!pw) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    micWS = new WebSocket(`${proto}//${location.host}/mic/${encodeURIComponent(key)}?token=${encodeURIComponent(pw)}`);
    micWS.binaryType = "arraybuffer";
    micWS.onopen = () => { micWS.send(JSON.stringify({ kind: "mic_start" })); };
    micWS.onclose = (e) => {
      if (e.code === 1006 || e.code === 1008) {
        // 인증 실패 추정 → 저장된 비번 폐기
        localStorage.removeItem(ADMIN_KEY);
        alert("admin 인증 실패 — 비밀번호를 다시 입력하세요.");
        micStop();
        adminToggle.textContent = "🔒 admin";
        micToggle.classList.add("admin-hidden");
      }
    };
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    micCtx = new (window.AudioContext || window.webkitAudioContext)();
    const srcNode = micCtx.createMediaStreamSource(micStream);
    micNode = micCtx.createScriptProcessor(4096, 1, 1);
    srcNode.connect(micNode);
    micNode.connect(micCtx.destination);
    micLevel.classList.remove("admin-hidden");
    micNode.onaudioprocess = (ev) => {
      const inp = ev.inputBuffer.getChannelData(0);
      let peak = 0; for (let i = 0; i < inp.length; i++) peak = Math.max(peak, Math.abs(inp[i]));
      document.getElementById("mic-bar").style.width = Math.min(100, peak * 140) + "%";
      if (!micWS || micWS.readyState !== 1) return;
      micWS.send(floatToInt16(downsample(inp, micCtx.sampleRate)).buffer);
    };
  }
  function micStop() {
    try { if (micWS && micWS.readyState === 1) micWS.send(JSON.stringify({ kind: "mic_stop" })); } catch {}
    if (micNode) { micNode.disconnect(); micNode.onaudioprocess = null; micNode = null; }
    if (micCtx) { micCtx.close(); micCtx = null; }
    if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
    if (micWS) { try { micWS.close(); } catch {} micWS = null; }
    document.getElementById("mic-bar").style.width = "0%";
    micLevel.classList.add("admin-hidden");
  }
  micToggle.addEventListener("click", async () => {
    micOn = !micOn;
    micToggle.textContent = micOn ? "🎙️ 마이크 끄기" : "🎙️ 마이크 켜기";
    micToggle.classList.toggle("off", micOn);
    if (micOn) {
      try { await micStart(); }
      catch (e) { alert("마이크 권한 실패: " + e.message); micOn = false; micToggle.textContent = "🎙️ 마이크 켜기"; micToggle.classList.remove("off"); }
    } else micStop();
  });
```

- [ ] **Step 5: 타입체크(번들 무결성) + 커밋**

Run: `cd meeting-web && npm run typecheck` → 오류 없음 (HTML 은 텍스트 번들이라 tsc 영향 없음; 무결성 확인용)
```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/src/static/meeting.html
git commit -m "feat(meeting-web): 뷰어에 admin 인증 + mic 토글 + 소스 배지 통합"
```

---

## Task 8: 통합 검증 스크립트 확장

`mic_relay_check.mjs` 에 (a) `/mic` 은 ADMIN_PASSWORD 로만 통과, (b) receiver 가 `mic_source` 송신 → viewer 수신, (c) 신규 viewer 가 `lastMicSource` 동기화 수신을 추가한다.

**Files:**
- Modify: `meeting-web/scripts/mic_relay_check.mjs`

- [ ] **Step 1: 스크립트 교체**

`meeting-web/scripts/mic_relay_check.mjs` 전체를 다음으로 교체:
```javascript
// meeting-web/scripts/mic_relay_check.mjs
// 사용법: 터미널 1) cd meeting-web && npm run dev   (.dev.vars 에 RELAY_TOKEN, ADMIN_PASSWORD)
//         터미널 2) cd meeting-web && RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs
import WebSocket from "ws";

const BASE = process.env.BASE || "ws://localhost:8787";
const RELAY = process.env.RELAY_TOKEN || "devtoken";
const ADMIN = process.env.ADMIN_PASSWORD || "adminpw";
const KEY = "checkroom";

function open(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, opts);
    ws.on("open", () => resolve(ws));
    ws.on("error", reject);
  });
}
function nextMsg(ws) {
  return new Promise((res) => ws.on("message", (d, isBinary) => res({ isBinary, text: isBinary ? null : d.toString() })));
}
const fail = (m) => { console.error("FAIL", m); process.exit(1); };

async function main() {
  // 1) /mic 은 RELAY_TOKEN 으로는 거부, ADMIN_PASSWORD 로만 통과
  let relayRejected = false;
  try { await open(`${BASE}/mic/${KEY}?token=${RELAY}`); } catch { relayRejected = true; }
  console.log("relay-token /mic 거부:", relayRejected ? "OK" : "FAIL");

  // 2) jarvis 수신측(/mic-recv, RELAY_TOKEN) 연결
  const recv = await open(`${BASE}/mic-recv/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });

  // 3) admin 송신측(/mic, ADMIN_PASSWORD) 연결 + binary 포워딩
  const recvBin = nextMsg(recv);
  const send = await open(`${BASE}/mic/${KEY}?token=${ADMIN}`);
  send.send(Buffer.from(new Int16Array([1, 2, 3, 4]).buffer));
  const b = await Promise.race([recvBin, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("binary 포워딩(admin):", b.isBinary ? "OK" : "FAIL");

  // 4) viewer 접속 후, receiver 가 mic_source 송신 → viewer 가 수신
  const viewer = await open(`${BASE}/subscribe/${KEY}`);
  const vMsg = (async () => {
    for (;;) {
      const m = await nextMsg(viewer);
      if (m.text && m.text.includes('"mic_source"')) return m.text;
    }
  })();
  recv.send(JSON.stringify({ kind: "mic_source", source: "remote" }));
  const vm = await Promise.race([vMsg, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("mic_source broadcast:", vm.includes('"source":"remote"') || vm.includes('"source": "remote"') ? "OK" : `FAIL (${vm})`);

  // 5) 신규 viewer 가 lastMicSource 동기화 수신
  const viewer2 = await open(`${BASE}/subscribe/${KEY}`);
  const v2 = (async () => {
    for (;;) {
      const m = await nextMsg(viewer2);
      if (m.text && m.text.includes('"mic_source"')) return m.text;
    }
  })();
  const sync = await Promise.race([v2, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("신규 viewer 동기화:", sync.includes("remote") ? "OK" : `FAIL (${sync})`);

  [recv, send, viewer, viewer2].forEach((w) => w.close());
  process.exit(0);
}
main().catch((e) => fail(e));
```

- [ ] **Step 2: best-effort 라이브 런**

`.dev.vars` 에 `RELAY_TOKEN=devtoken` 과 `ADMIN_PASSWORD=adminpw` 두 줄을 둔다(없으면 생성; `.gitignore` 에 이미 제외됨, 커밋 금지).
1. `cd meeting-web && npx wrangler dev --port 8787 &>/tmp/wrangler.log &` (8~10초 대기)
2. `cd meeting-web && RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs`
3. 기대 출력:
```
relay-token /mic 거부: OK
binary 포워딩(admin): OK
mic_source broadcast: OK
신규 viewer 동기화: OK
```
4. 끝나면 wrangler 종료(`pkill -f wrangler`).

`wrangler dev` 가 이 환경에서 안 뜨면(네트워크/workerd) 스킵하고 그 사유를 보고한다 — 커밋된 스크립트가 산출물.

- [ ] **Step 3: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/scripts/mic_relay_check.mjs
git commit -m "test(meeting-web): admin 인증 + mic_source 흐름 검증 추가"
```

---

## Task 9: 배포 안내 (수동, 비코드)

- [ ] **Step 1: README/배포 메모**

`meeting-web/.dev.vars.example` 에 `ADMIN_PASSWORD` 가 없으면 한 줄 추가:
```
ADMIN_PASSWORD=changeme
```
(파일이 없으면 생성.) 그리고 배포 시 `cd meeting-web && wrangler secret put ADMIN_PASSWORD` 가 필요함을 커밋 메시지/이 플랜에 남긴다.

- [ ] **Step 2: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/.dev.vars.example
git commit -m "docs(meeting-web): ADMIN_PASSWORD dev 예시 추가"
```

- [ ] **Step 3: 수동 E2E (배포 후, 사용자 확인)**

1. `cd meeting-web && wrangler secret put ADMIN_PASSWORD` 로 비밀 설정 후 `npm run deploy`.
2. jarvis 를 `REMOTE_MIC_ENABLED=true RELAY_URL=wss://<...>.workers.dev RELAY_TOKEN=<...> REMOTE_MIC_KEY=<room> python main.py` 로 실행.
3. 콘솔 박스의 `/m/<room>` URL 을 폰/노트북에서 열기 → 🔒 admin → 비밀번호 입력 → 🎙️ 마이크 켜기.
4. 폰에 대고 "Hey Jarvis" → 자비스가 깨어남 + 소스 배지가 "🎚️ 원격(폰)" 으로.
5. 마이크 끄기 → idle 후 배지가 "🎚️ 시스템" 으로 복귀.

---

## Self-Review 결과

**Spec coverage:**
- ADMIN_PASSWORD 인증(RELAY_TOKEN 분리) → Task 5 ✓
- /mic→ADMIN, /mic-recv→RELAY → Task 5 ✓
- 뷰어 통합 mic 토글 + 캡처 이식 → Task 7 ✓
- capture.html/route 제거 → Task 5 ✓
- 소스 배지(실시간) → Task 7(렌더) + Task 6(broadcast) + Task 1/2/3(jarvis 보고) ✓
- 신규 viewer 동기화(lastMicSource) → Task 6 ✓
- MicRouter.on_switch → Task 1 ✓
- receiver 양방향/notify_source/재연결 동기화 → Task 2 ✓
- main 배선 + URL 박스 /m → Task 3 ✓
- types mic_source/source → Task 4 ✓
- 검증(단위/통합/수동) → Task 1·2 단위, Task 8 통합, Task 9 수동 ✓
- 엣지(REMOTE_MIC_ENABLED=false, 비번 오류, 재연결, last-wins) → Task 6(null 시 미표시)·Task 7(401 처리)·Task 2(재연결 송신) ✓

**연기(스펙대로):** 세션/쿠키 로그인, admin 롤 다중화, TTS→폰 출력.

**Type consistency:** `on_switch(source: str)` / `notify_source(source)` / `_outbound`·`_last_source` / `mic_source`+`source` 필드 — Task 간 일치. meeting-web `requireAdmin`/`requireRelayToken`/`checkSecret`, `lastMicSource`, `mic_source` kind 일치.

**알려진 한계:** ADMIN_PASSWORD 가 localStorage/URL 쿼리에 남음(토이 트레이드오프, 문서화). 비번 오류 감지는 WS close code(1006/1008) 추정 기반 — 완벽하진 않으나 토이 범위 충분.
