# 회의 종료 동기화 + 웹 종료 버튼 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의가 음성/`/stop`/웹 버튼 어느 경로로 끝나든 jarvis 종료 + 웹 홈 복귀로 수렴하고, 웹에서 회의를 끝낼 수 있게 한다.

**Architecture:** 회의 종료의 단일 진실 지점을 `stop_meeting()` 으로 삼아 거기서 `navigate("home")` 발행. 브라우저→jarvis 전용 control 채널(`/control` + `/control-recv`, 마이크 채널 패턴 미러)을 신설해 웹의 `meeting_stop` 명령을 `ControlReceiver` 가 받아 `stop_meeting()` 호출.

**Tech Stack:** Python 3.11 (pytest, websockets) · Cloudflare Worker(TS, Hono, Durable Object) · 바닐라 JS.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`. typecheck: `cd jarvis-web && npm run typecheck`. 로컬 워커: `cd jarvis-web && npx wrangler dev --port 8787`.

---

## Task 1: control_receiver.py — 웹 제어 수신기 + 단위 테스트

**Files:** Create `control_receiver.py`, Create `tests/test_control_receiver.py`

`RemoteMicReceiver` 의 JSON 전용 축소판. `/control-recv/<key>` 에 상시 연결, `{kind:"meeting_stop"}` 수신 시 `on_command` 콜백 호출.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_control_receiver.py`:
```python
# tests/test_control_receiver.py
import asyncio
from control_receiver import ControlReceiver


def _rx(calls):
    async def on_command(kind):
        calls.append(kind)
    return ControlReceiver("ws://x", "tok", on_command=on_command,
                           on_log=lambda *a: None, key="k")


def test_meeting_stop_dispatches():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"meeting_stop"}'))
    assert calls == ["meeting_stop"]


def test_other_kinds_ignored():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"something_else"}'))
    asyncio.run(rx._handle_message("not json at all"))
    asyncio.run(rx._handle_message('{"no":"kind"}'))
    assert calls == []
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_control_receiver.py -v` → FAIL (ModuleNotFoundError: control_receiver).

- [ ] **Step 3: 구현** — `control_receiver.py`:
```python
# control_receiver.py
"""relay 의 /control-recv/<key> 에 붙어 브라우저발 제어 명령(JSON)을 받는 인바운드 클라이언트.

remote_mic_receiver.py 와 대칭이되 오디오/큐가 없는 JSON 전용. RELAY 설정 시 상시 연결,
끊기면 지수 백오프 재연결. `{kind:"meeting_stop"}` 수신 시 on_command("meeting_stop") 호출.
"""
import asyncio
import json
from urllib.parse import quote

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore


class ControlReceiver:
    def __init__(self, url, token, *, on_command, on_log=print, key=None,
                 connect_timeout=5.0):
        self.base_url = url.rstrip("/")
        self.token = token
        self.on_command = on_command
        self.on_log = on_log
        self.key = key
        self.connect_timeout = connect_timeout
        self._stop = asyncio.Event()
        self._task = None

    def _url(self):
        key = quote(self.key or "jarvis", safe="")
        return f"{self.base_url}/control-recv/{key}"

    async def _handle_message(self, data):
        if isinstance(data, (bytes, bytearray)):
            return
        try:
            msg = json.loads(data)
        except Exception:
            return
        kind = msg.get("kind")
        if kind == "meeting_stop":
            await self.on_command("meeting_stop")
        elif kind == "no_receiver":
            self.on_log("[control] relay: 수신자 없음 통지")

    def start(self):
        if websockets is None:
            self.on_log("[control] websockets 미설치 — 웹 제어 비활성")
            return None
        self._task = asyncio.create_task(self._run(), name="control-rx")
        return self._task

    async def close(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _recv_loop(self, ws) -> None:
        async for message in ws:
            await self._handle_message(message)

    async def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 0.5
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_log(f"[control] 수신 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _connect_once(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self.on_log("[control] 웹 제어 수신 대기 중")
            await self._recv_loop(ws)
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_control_receiver.py -v` (2 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 5: 커밋**
```bash
git add control_receiver.py tests/test_control_receiver.py
git commit -m "feat: ControlReceiver — 웹 제어(/control-recv) 수신기 + 테스트"
```

---

## Task 2: main.py — control_rx 배선 + stop_meeting navigate home

**Files:** Modify `main.py`

- [ ] **Step 1: control_rx 초기화 변수** — 현재 74-75행:
```python
    remote_mic_rx = None
    remote_mic_monitor = None
```
을 다음으로 교체(변수 추가):
```python
    remote_mic_rx = None
    remote_mic_monitor = None
    control_rx = None
```

- [ ] **Step 2: control_rx 생성·배선** — 원격 마이크 블록 끝(91행 `remote_mic_monitor = asyncio.create_task(mic.router.run_idle_monitor())`) **다음**, `state = "WAITING_WAKE"`(92행) **앞**에 삽입:
```python
    # 웹 제어 채널(브라우저 → jarvis): 회의 종료 등 비오디오 명령. REMOTE_MIC 와 독립.
    if config.RELAY_URL and config.RELAY_TOKEN:
        from control_receiver import ControlReceiver

        async def _on_remote_command(kind):
            if kind == "meeting_stop":
                await stop_meeting()

        control_rx = ControlReceiver(
            config.RELAY_URL, config.RELAY_TOKEN,
            on_command=_on_remote_command, on_log=console.log,
            key=config.ROOM_KEY, connect_timeout=config.RELAY_TIMEOUT_S,
        )
        control_rx.start()
```
(`_on_remote_command` 가 참조하는 `stop_meeting` 은 정의가 아래(429행)지만, 호출은 control_rx 수신 시점(런타임)이라 클로저로 존재 — 기존 `_handle_mode` 와 동일 패턴.)

- [ ] **Step 3: `_handle_mode` 중복 navigate 제거** — 현재 193-196행:
```python
        else:  # "stop"
            await stop_meeting()
            if web_pub is not None:
                web_pub.emit("navigate", "home")
```
을 다음으로 교체(navigate 발행 제거 — stop_meeting 이 책임):
```python
        else:  # "stop"
            await stop_meeting()
```

- [ ] **Step 4: stop_meeting 에서 navigate home 발행** — 현재 `stop_meeting` 의 finally(439-443행):
```python
        try:
            await sess.stop()
        finally:
            mic.router.set_tap(None)
            meeting_session["obj"] = None
            console.set_status(None)
            idle()
```
을 다음으로 교체(navigate home 추가 — 실제 종료 시점):
```python
        try:
            await sess.stop()
        finally:
            mic.router.set_tap(None)
            meeting_session["obj"] = None
            console.set_status(None)
            if web_pub is not None:
                web_pub.emit("navigate", "home")
            idle()
```

- [ ] **Step 5: 종료 시 control_rx.close** — 현재 finally 의 remote_mic_rx 정리(546-550행):
```python
        if remote_mic_rx is not None:
            try:
                await remote_mic_rx.close()
            except Exception:
                pass
```
**다음**에 추가:
```python
        if control_rx is not None:
            try:
                await control_rx.close()
            except Exception:
                pass
```

- [ ] **Step 6: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 7: 커밋**
```bash
git add main.py
git commit -m "feat: main — control_rx 배선 + stop_meeting 에서 navigate home(모든 종료 경로 통지)"
```

---

## Task 3: relay — control 채널 (index.ts + meeting_do.ts)

**Files:** Modify `jarvis-web/src/index.ts`, `jarvis-web/src/meeting_do.ts`

- [ ] **Step 1: index.ts — forwardToDO role 유니온** — 95행:
```ts
function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv", original: Request): Promise<Response> {
```
을 다음으로 교체:
```ts
function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv" | "control" | "control-recv", original: Request): Promise<Response> {
```

- [ ] **Step 2: index.ts — control 라우트** — `/mic-recv/:key` 핸들러(닫는 `});`) **다음**, `/:name/meeting` 핸들러 **앞**에 삽입:
```ts
app.get("/control/:key", async (c) => {
  if (!requireAdmin(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "control", c.req.raw);
});

app.get("/control-recv/:key", async (c) => {
  if (!requireRelayToken(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "control-recv", c.req.raw);
});
```

- [ ] **Step 3: meeting_do.ts — 슬롯 필드** — 21-22행:
```ts
  private micSender: WebSocket | null = null;
  private micReceiver: WebSocket | null = null;
```
**다음**에 추가:
```ts
  private controlSender: WebSocket | null = null;
  private controlReceiver: WebSocket | null = null;
  private lastControlNoReceiverAt = 0;
```

- [ ] **Step 4: meeting_do.ts — fetch role 화이트리스트 + 분기** — 40행:
```ts
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv") {
```
을 다음으로 교체:
```ts
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv" && role !== "control" && role !== "control-recv") {
```
그리고 51-59행의 role 분기:
```ts
    if (role === "publish") {
      this.attachPublisher(server);
    } else if (role === "subscribe") {
      this.attachViewer(server);
    } else if (role === "mic") {
      this.attachMicSender(server);
    } else {
      this.attachMicReceiver(server);
    }
```
을 다음으로 교체:
```ts
    if (role === "publish") {
      this.attachPublisher(server);
    } else if (role === "subscribe") {
      this.attachViewer(server);
    } else if (role === "mic") {
      this.attachMicSender(server);
    } else if (role === "mic-recv") {
      this.attachMicReceiver(server);
    } else if (role === "control") {
      this.attachControlSender(server);
    } else {
      this.attachControlReceiver(server);
    }
```

- [ ] **Step 5: meeting_do.ts — attach 메서드** — `attachMicReceiver` 메서드(213-214행의 닫는 `}` 두 줄: `ws.addEventListener("error", () => { if (this.micReceiver === ws) this.micReceiver = null; });` 와 그 뒤 메서드 닫는 `}`) **다음**에 두 메서드 추가:
```ts

  private attachControlSender(ws: WebSocket): void {
    if (this.controlSender) {
      try {
        this.safeSend(this.controlSender, this.buildEvent({ kind: "kicked", reason: "replaced" }));
        this.controlSender.close(1000, "replaced");
      } catch { /* */ }
    }
    this.controlSender = ws;
    ws.addEventListener("message", (msg) => {
      const data = (msg as MessageEvent).data as string | ArrayBuffer;
      if (!this.controlReceiver) {
        const now = Date.now();
        if (now - this.lastControlNoReceiverAt > 2000) {
          this.lastControlNoReceiverAt = now;
          this.safeSend(ws, { ts: now / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
        }
        return;
      }
      try { this.controlReceiver.send(data); } catch { /* 수신측 끊김 */ }
    });
    ws.addEventListener("close", () => { if (this.controlSender === ws) this.controlSender = null; });
    ws.addEventListener("error", () => { if (this.controlSender === ws) this.controlSender = null; });
  }

  private attachControlReceiver(ws: WebSocket): void {
    if (this.controlReceiver) {
      try { this.controlReceiver.close(1000, "replaced"); } catch { /* */ }
    }
    this.controlReceiver = ws;
    ws.addEventListener("close", () => { if (this.controlReceiver === ws) this.controlReceiver = null; });
    ws.addEventListener("error", () => { if (this.controlReceiver === ws) this.controlReceiver = null; });
  }
```

- [ ] **Step 6: 검증 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `cd /Users/oracle/Documents/concode/jarvis && grep -c 'control-recv\|attachControlSender\|attachControlReceiver' jarvis-web/src/meeting_do.ts` → `3` 이상; `grep -c '/control' jarvis-web/src/index.ts` → `2` 이상
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/index.ts jarvis-web/src/meeting_do.ts
git commit -m "feat(jarvis-web): control 채널 — /control·/control-recv 라우트 + DO forward"
```

---

## Task 4: web app.html — 제목 + 회의 종료 버튼

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: 제목 — applyMeta** — `applyMeta` 안의 현재:
```js
    $("title").textContent = `🎤 ${partner} ↔ ${user}`;
```
을 다음으로 교체:
```js
    $("title").textContent = partner ? `🎤 ${partner} ↔ ${user}` : "🎤 Meeting";
```

- [ ] **Step 2: 버튼 CSS** — `<style>` 안 `#mic-bar { ... }` 규칙 **다음** 줄에 추가:
```css
  #meeting-stop { display: none; background: #dc2626; }
  body[data-view="meeting"] #meeting-stop { display: inline-block; }
```

- [ ] **Step 3: 버튼 DOM** — `#controls` div 안, `#mic-level` div **다음**에 버튼 추가. 현재:
```html
  <div id="controls">
    <button id="mic-toggle">🎙️ 마이크 켜기</button>
    <div id="mic-level" class="hidden"><div id="mic-bar"></div></div>
  </div>
```
을 다음으로 교체:
```html
  <div id="controls">
    <button id="mic-toggle">🎙️ 마이크 켜기</button>
    <div id="mic-level" class="hidden"><div id="mic-bar"></div></div>
    <button id="meeting-stop">🛑 회의 종료</button>
  </div>
```

- [ ] **Step 4: 버튼 클릭 핸들러** — mic-toggle 클릭 핸들러(`$("mic-toggle").addEventListener("click", async () => { ... });`) **다음**, `// ---- 초기화 ----` 주석 **앞**에 추가:
```js
  // ---- 회의 종료 (웹 → control 채널, one-shot) ----
  $("meeting-stop").addEventListener("click", () => {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    try {
      const cws = new WebSocket(`${proto}//${location.host}/control/${encodeURIComponent(name)}?token=${encodeURIComponent(pw)}`);
      cws.onopen = () => {
        try { cws.send(JSON.stringify({ kind: "meeting_stop" })); }
        finally { setTimeout(() => { try { cws.close(); } catch {} }, 200); }
      };
      cws.onerror = () => {};
    } catch {}
  });
```
(뷰 전환은 하지 않음 — jarvis 가 stop_meeting→navigate("home") 되쏘면 기존 navigate 핸들러가 `showView("home")`.)

- [ ] **Step 5: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/app2.js && node --check /tmp/app2.js && echo "JS OK"` → `JS OK`
`grep -c 'id="meeting-stop"\|meeting_stop\|🎤 Meeting' jarvis-web/src/static/app.html` → `4` 이상 (버튼 DOM 1 + CSS 2 + 핸들러 send 1 + 제목 1 중 매칭)
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(jarvis-web): 회의 종료 버튼 + 제목 '🎤 Meeting'"
```

---

## Task 5: 통합 검증 + 배포

**Files:** Modify `jarvis-web/scripts/mic_relay_check.mjs`

- [ ] **Step 1: control forward 검증 추가** — `mic_relay_check.mjs` 의 `main()`, 마지막 정리(`[recv, send, send2, viewer, viewer2].forEach(...)`) **직전**에 삽입:
```javascript
  // 12) control 채널: sender(/control) → receiver(/control-recv) forward
  const ctlRecv = await open(`${BASE}/control-recv/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });
  const ctlGot = nextMsg(ctlRecv);
  const ctlSend = await open(`${BASE}/control/${KEY}?token=${ADMIN}`);
  ctlSend.send(JSON.stringify({ kind: "meeting_stop" }));
  const cm = await Promise.race([ctlGot, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("control forward:", cm.text && cm.text.includes("meeting_stop") ? "OK" : `FAIL (${cm.text})`);
  ctlSend.close(); ctlRecv.close();

```

- [ ] **Step 2: best-effort 라이브 런** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s 부팅 대기, `/tmp/jw.log` 에서 "Ready" 확인) → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 모든 줄 OK 기대(신규 "control forward: OK" 포함) → `pkill -f "wrangler dev"`. 안 뜨면 스킵+사유. `node --check scripts/mic_relay_check.mjs` 로 구문 항상 검증.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/scripts/mic_relay_check.mjs
git commit -m "test(jarvis-web): control 채널 forward 검증"
```

- [ ] **Step 4: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 폰 회의 뷰 → 🛑 회의 종료 → jarvis 회의 종료 + 폰 홈 복귀; jarvis 콘솔 `/stop` → 폰 홈 복귀; 회의 진입 시 제목 "🎤 Meeting".

---

## Self-Review 결과

**Spec coverage:**
- #1 제목 "🎤 Meeting" → Task 4 Step 1 ✓
- #2 stop_meeting navigate home + _handle_mode 중복 제거 → Task 2 Step 3·4 ✓
- #3 control 채널: ControlReceiver(jarvis) → Task 1, main 배선 → Task 2, relay 라우트/DO → Task 3, 웹 버튼 → Task 4 ✓
- 통합 검증 + 수동 E2E + 배포 → Task 5 ✓
- 비범위(다른 control 명령, 상대 이름 복원) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 에 완전한 코드 포함. 빈칸 없음.

**Type/이름 consistency:** `meeting_stop` 문자열이 4곳 일관 — 웹 send `{kind:"meeting_stop"}` ↔ DO 통과(파싱 안 함) ↔ `ControlReceiver._handle_message` `kind=="meeting_stop"` ↔ `_on_remote_command("meeting_stop")` → `stop_meeting()`. 라우트 `/control`·`/control-recv` ↔ DO role `control`/`control-recv` ↔ `attachControlSender`/`attachControlReceiver` ↔ `ControlReceiver._url()` `/control-recv/{key}`. `navigate("home")` ↔ app.html navigate 핸들러 `ev.text==="home"`→showView("home")(기존).

**핵심 위험:** (1) `_on_remote_command` 가 참조하는 stop_meeting 은 정의가 아래지만 호출 시점엔 클로저로 존재(Task2 Step2 주석). (2) navigate home 을 stop_meeting finally 로 옮겨 모든 경로 통지 — 단, setup 취소/회의 아님 조기 반환은 finally 전 return 이라 미발행(의도대로). (3) control sender 는 one-shot 이라 슬롯 충돌 없음, 마이크 슬롯과 분리.
