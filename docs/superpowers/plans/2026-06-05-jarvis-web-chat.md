# jarvis-web 채팅 홈 (B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** jarvis-web 홈을 음성 대화 채팅으로 — jarvis 대화를 웹으로 발행(🧑/🤖 버블)하고 TTS 오디오를 마이크가 있는 곳(폰)으로 스트리밍한다.

**Architecture:** jarvis 시작 시 상시 `RelayClient`(web_pub) 1개를 `/publish/<ROOM_KEY>` 에 연결. `speak_response` 가 user/assistant 텍스트를 emit 하고, TTS 는 `mic.router.active=="remote"` 면 `emit_audio`(binary)로 웹 전송+로컬 skip, 아니면 로컬 재생. DO 는 publisher 의 binary 를 viewer 에게 broadcast. 홈은 JSON→버블, binary→Web Audio 재생. 회의는 web_pub 을 재사용(슬롯 단일화).

**Tech Stack:** Python 3.11 (asyncio, numpy, websockets, pytest) · Cloudflare Workers (Hono+DO, TS) · 바닐라 JS Web Audio.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`. typecheck: `cd jarvis-web && npm run typecheck`.

---

## Task 1: RelayClient.emit_audio + send 루프 binary 분기

**Files:** `relay_client.py`, `tests/test_relay_client.py`(신규)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_relay_client.py`:
```python
# tests/test_relay_client.py
import asyncio
import struct

from relay_client import RelayClient


def _rc():
    return RelayClient("ws://x", "tok", {"key": "room"}, on_log=lambda *_: None)


def test_emit_audio_enqueues_binary_with_sr_header():
    rc = _rc()
    rc.emit_audio(b"\x01\x02\x03\x04", 22050)
    item = rc._queue.get_nowait()
    assert isinstance(item, (bytes, bytearray))
    assert struct.unpack("<I", item[:4])[0] == 22050
    assert bytes(item[4:]) == b"\x01\x02\x03\x04"


def test_emit_enqueues_json_dict():
    rc = _rc()
    rc.emit("assistant", "안녕")
    item = rc._queue.get_nowait()
    assert item == {"kind": "assistant", "text": "안녕"}


def test_send_item_routes_bytes_vs_json():
    rc = _rc()
    sent = []

    class FakeWS:
        async def send(self, data): sent.append(data)

    async def main():
        ws = FakeWS()
        await rc._send_item(ws, {"kind": "user", "text": "hi"})
        await rc._send_item(ws, struct.pack("<I", 16000) + b"\xaa\xbb")

    asyncio.run(main())
    assert sent[0] == '{"kind": "user", "text": "hi"}'   # JSON 문자열
    assert isinstance(sent[1], (bytes, bytearray)) and sent[1][:4] == struct.pack("<I", 16000)
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_relay_client.py -v` → FAIL (`emit_audio`/`_send_item` 없음).

- [ ] **Step 3: 구현 (relay_client.py)**

(a) 상단 import 에 `import struct` 추가(이미 `import json`/`import asyncio` 있음).

(b) `emit_async` 메서드 아래에 `emit_audio` 추가:
```python
    def emit_audio(self, pcm_bytes: bytes, sr: int) -> None:
        """TTS PCM(int16 LE)을 binary 프레임으로 enqueue: [4B sr LE][int16 PCM]."""
        frame = struct.pack("<I", int(sr)) + bytes(pcm_bytes)
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.on_log("[relay] 큐 가득참 — 오디오 드롭")
```

(c) `_send_item` 헬퍼 추가(예: `_connect_once` 위):
```python
    async def _send_item(self, ws, item) -> None:
        if isinstance(item, (bytes, bytearray)):
            await ws.send(item)
        else:
            await ws.send(json.dumps(item, ensure_ascii=False))
```

(d) `_connect_once` 의 send 루프에서 송신부 교체. 현재:
```python
                try:
                    await ws.send(json.dumps(msg, ensure_ascii=False))
                except ConnectionClosed:
                    # 큐에 다시 넣고 바깥 _run 에서 재연결
                    try:
                        self._queue.put_nowait(msg)
                    except asyncio.QueueFull:
                        pass
                    raise
                if msg.get("kind") == "end":
                    # end 송신 후 깔끔 종료
                    return
```
교체:
```python
                try:
                    await self._send_item(ws, msg)
                except ConnectionClosed:
                    # 큐에 다시 넣고 바깥 _run 에서 재연결
                    try:
                        self._queue.put_nowait(msg)
                    except asyncio.QueueFull:
                        pass
                    raise
                if not isinstance(msg, (bytes, bytearray)) and msg.get("kind") == "end":
                    # end 송신 후 깔끔 종료
                    return
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_relay_client.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 5: 커밋**
```bash
git add relay_client.py tests/test_relay_client.py
git commit -m "feat: RelayClient.emit_audio — TTS PCM binary 프레임 발행"
```

---

## Task 2: relay(DO) publisher binary broadcast + types

**Files:** `jarvis-web/src/types.ts`, `jarvis-web/src/meeting_do.ts`

- [ ] **Step 1: types.ts — user/assistant kind 추가**

`EventKind` union 에 추가(`"mic_source"` 다음):
```typescript
  | "user"
  | "assistant"
```

- [ ] **Step 2: meeting_do.ts — publisher binary → broadcastBinary**

`attachPublisher` 의 message 핸들러를 교체. 현재:
```typescript
    ws.addEventListener("message", (msg) => {
      let parsed: ClientMessage | null = null;
      try {
        const raw = typeof msg.data === "string" ? msg.data : new TextDecoder().decode(msg.data);
        parsed = JSON.parse(raw) as ClientMessage;
      } catch {
        // 무효 메시지 무시
        return;
      }
      this.handlePublisherMessage(parsed);
    });
```
교체:
```typescript
    ws.addEventListener("message", (msg) => {
      const data = (msg as MessageEvent).data;
      if (data instanceof ArrayBuffer) {
        // TTS 오디오 — viewer 에게 raw binary broadcast (replay 버퍼 미적재)
        this.broadcastBinary(data);
        return;
      }
      let parsed: ClientMessage | null = null;
      try {
        parsed = JSON.parse(data as string) as ClientMessage;
      } catch {
        return;
      }
      this.handlePublisherMessage(parsed);
    });
```

- [ ] **Step 3: broadcastBinary 메서드 추가**

`broadcast` 메서드 아래에 추가:
```typescript
  private broadcastBinary(data: ArrayBuffer): void {
    for (const ws of this.viewers) {
      try { ws.send(data); } catch { /* 끊긴 소켓 — close 에서 정리 */ }
    }
  }
```

- [ ] **Step 4: 타입체크 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts jarvis-web/src/meeting_do.ts
git commit -m "feat(jarvis-web): publisher 오디오 binary 를 viewer 로 broadcast + user/assistant kind"
```

---

## Task 3: main.py — 상시 web_pub + speak_response 발행/라우팅 + 에코 게이트 + 회의 재사용

**Files:** `main.py`

통합 배선 — import/parse 스모크 + 전체 suite.

- [ ] **Step 1: import 추가**

`main.py` 상단 `import asyncio` 아래에 추가:
```python
import time

import numpy as np
```

- [ ] **Step 2: 상시 web_pub 생성 + 홈 URL 박스**

`player_task = asyncio.create_task(player.run())` 다음, `remote_mic` 블록 **앞**에 web_pub 생성을 추가:
```python
    # 상시 웹 퍼블리셔 (대화/TTS/회의자막 공용). RELAY 설정 시 항상 연결.
    web_pub = None
    web_speaking_until = 0.0   # 웹으로 TTS 재생 추정 종료 시각(에코 게이트)
    if config.RELAY_URL and config.RELAY_TOKEN:
        from relay_client import RelayClient
        from live_translate import MeetingMeta
        web_pub = RelayClient(
            config.RELAY_URL, config.RELAY_TOKEN, MeetingMeta(my_name=config.USER_NAME),
            on_log=console.log, connect_timeout=config.RELAY_TIMEOUT_S,
        )
        await web_pub.connect()
        home_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
        home_url = f"{home_base}/{config.ROOM_KEY}"
        bw = max(len(home_url) + 4, 60); border = "─" * bw
        console.log(""); console.log(f"┌{border}┐")
        console.log(f"│  🤖 Jarvis 웹 (로그인 후 대화/마이크)".ljust(bw + 1) + "│")
        console.log(f"│  {home_url}".ljust(bw + 1) + "│")
        console.log(f"└{border}┘"); console.log("")
```
그리고 기존 `remote_mic` 블록 안의 **URL 박스 출력 부분**(`cap_base = ...` 부터 마지막 `console.log("")` 까지)을 **삭제**한다(이제 web_pub 박스가 홈 URL 을 보여줌). `remote_mic_rx.start()` / `mic.router.on_switch=...` / `run_idle_monitor` 배선은 유지.

- [ ] **Step 3: speak_response 교체 (텍스트 발행 + TTS 라우팅)**

현재 `speak_response` 본문(console.log 부터 finally 까지)을 교체:
```python
    async def speak_response(text: str):
        """입력 텍스트 → LLM → TTS. 텍스트/오디오를 웹으로도 발행.
        TTS 는 원격 마이크 활성 시 웹(폰)으로만, 아니면 로컬 스피커로."""
        nonlocal web_speaking_until
        console.log(f"🧑 {text}")
        if web_pub is not None:
            web_pub.emit("user", text)
        console.set_status("생각 중…")
        first = True
        try:
            async for sentence in llm.respond(text):
                if first:
                    console.set_status(None)
                prefix = "🤖 " if first else "   "
                console.log(f"{prefix}{sentence}")
                first = False
                if web_pub is not None:
                    web_pub.emit("assistant", sentence)
                wav, sr = await tts.synth(sentence)
                if web_pub is not None and mic.router.active == "remote":
                    pcm16 = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                    web_pub.emit_audio(pcm16, sr)
                    dur = len(wav) / float(sr)
                    web_speaking_until = max(web_speaking_until, time.monotonic()) + dur
                else:
                    await player.enqueue(wav, sr)
        finally:
            console.set_status(None)
        if first:
            pass
```

- [ ] **Step 4: is_speaking 에코 게이트**

`mic.events(...)` 호출의 `is_speaking=player.is_speaking` 를 교체. 현재:
```python
        async for kind, audio in mic.events(
            wake_detect=wake.detect, is_speaking=player.is_speaking
        ):
```
교체:
```python
        async for kind, audio in mic.events(
            wake_detect=wake.detect,
            is_speaking=lambda: player.is_speaking() or time.monotonic() < web_speaking_until,
        ):
```

- [ ] **Step 5: 회의가 web_pub 재사용 (per-meeting RelayClient 제거)**

`_begin_meeting` 의 relay 블록 전체를 교체. 현재:
```python
            # 외부 중계 활성 (옵션) — 자막 페이지 URL 을 박스로 강조 표시
            if config.RELAY_URL and config.RELAY_TOKEN:
                from relay_client import RelayClient
                relay = RelayClient(
                    config.RELAY_URL, config.RELAY_TOKEN, meta,
                    on_log=console.log,
                    connect_timeout=config.RELAY_TIMEOUT_S,
                )
                ok = await relay.connect()
                if ok:
                    sess.add_listener(relay.emit_async)
                    sess._relay = relay   # stop() 에서 close
                    # http 보기 URL 안내 (ws → http 로 단순 치환)
                    view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
                    view_url = f"{view_base}/{meta.key}/meeting"
                    box_width = max(len(view_url) + 4, 60)
                    border = "─" * box_width
                    console.log("")
                    console.log(f"┌{border}┐")
                    console.log(f"│  🌐 자막 페이지 (이 URL 을 참석자에게 공유)".ljust(box_width + 1) + "│")
                    console.log(f"│  {view_url}".ljust(box_width + 1) + "│")
                    console.log(f"└{border}┘")
                    console.log("")
                else:
                    console.log("🌐 중계 서버 연결 실패 — 콘솔만으로 진행합니다.")
```
교체:
```python
            # 상시 web_pub 으로 자막 중계 (회의 전용 연결을 따로 만들지 않음)
            if web_pub is not None:
                sess.add_listener(web_pub.emit_async)
                view_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
                view_url = f"{view_base}/{meta.key}/meeting"
                console.log(f"🌐 자막: {view_url}")
```
(주의: `sess._relay = relay` 가 사라졌으므로, `MeetingSession.stop()` 이 `self._relay` 를 close 하려 해도 None 이면 안전해야 한다 — 아래 Step 6 확인.)

- [ ] **Step 6: MeetingSession.stop 의 _relay close 가 None 안전한지 확인 + 종료 정리**

`grep -n "_relay" live_translate.py` 로 stop() 의 `self._relay` 처리를 확인. `if self._relay is not None:` 가드가 있으면 그대로(우리는 더 이상 _relay 를 안 set 하므로 None → skip). 가드가 없으면 `if self._relay:` 가드를 추가한다. (web_pub 는 listener 만 추가했고, web_pub 의 생명주기는 main 이 소유 — 회의 종료 시 close 하지 않는다.)

main 종료부(`finally` 블록, `player_task.cancel()` 근처)에 web_pub 정리 추가:
```python
        if web_pub is not None:
            try:
                await web_pub.close()
            except Exception:
                pass
```

- [ ] **Step 7: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed
Run: `grep -n "RelayClient(" main.py` → web_pub 생성 1곳만(회의 블록의 per-meeting 생성은 사라짐)

- [ ] **Step 8: 커밋**
```bash
git add main.py
git commit -m "feat: main — 상시 web_pub 으로 대화/TTS 웹 발행, 에코 게이트, 회의 재사용"
```

---

## Task 4: home.html — 채팅 버블 + Web Audio 재생

**Files:** `jarvis-web/src/static/home.html`

A 의 홈(로그인+mic-take+배지+nav)은 유지. 채팅 placeholder 를 실제 채팅으로 + 오디오 재생.

- [ ] **Step 1: 채팅 CSS + 컨테이너**

`<style>` 끝(닫는 `</style>` 직전)에 버블 CSS 추가:
```css
  #chat { flex: 1; padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; opacity: 1; }
  .bubble { max-width: 80%; padding: 8px 12px; border-radius: 14px; white-space: pre-wrap; line-height: 1.4; }
  .bubble.user { align-self: flex-end; background: #2563eb; color: #fff; }
  .bubble.assistant { align-self: flex-start; background: #8883; }
```
`<main id="chat">💬 ...</main>` 의 placeholder 텍스트를 비운다: `<main id="chat"></main>`.

- [ ] **Step 2: 오디오 + 버블 로직 (스크립트 IIFE 안)**

IIFE 안, 기존 `connect()` 정의 부근에 오디오/버블 헬퍼를 추가하고, `connect()` 의 `ws.addEventListener("message", ...)` 핸들러를 확장한다.

(a) IIFE 상단(`let ws = null, reconnectDelay = 500;` 근처)에 추가:
```javascript
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
  const chat = $("chat");
  let lastRole = null, lastBubble = null;
  function addText(role, text) {
    if (role === lastRole && lastBubble) { lastBubble.textContent += " " + text; }
    else {
      const b = document.createElement("div");
      b.className = "bubble " + role; b.textContent = text;
      chat.appendChild(b); lastRole = role; lastBubble = b;
    }
    chat.scrollTop = chat.scrollHeight;
  }
```

(b) `connect()` 의 WS open 직후 `ws.binaryType = "arraybuffer";` 설정 — `ws = new WebSocket(...)` 다음 줄에:
```javascript
    ws.binaryType = "arraybuffer";
```

(c) `connect()` 의 message 핸들러를 확장. 현재(A):
```javascript
    ws.addEventListener("message", (m) => {
      try {
        const ev = JSON.parse(m.data);
        if (ev.kind === "mic_source") {
          $("mic-src").textContent = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
          $("mic-src").classList.toggle("remote", ev.source === "remote");
        }
      } catch {}
    });
```
교체:
```javascript
    ws.addEventListener("message", (m) => {
      if (m.data instanceof ArrayBuffer) { playAudio(m.data); return; }
      try {
        const ev = JSON.parse(m.data);
        if (ev.kind === "user") addText("user", ev.text || "");
        else if (ev.kind === "assistant") addText("assistant", ev.text || "");
        else if (ev.kind === "mic_source") {
          $("mic-src").textContent = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
          $("mic-src").classList.toggle("remote", ev.source === "remote");
        }
      } catch {}
    });
```

(d) 오디오 자동재생 정책: 사용자 제스처에서 `ensureAudio()` 호출. `login-go` 클릭 핸들러와 `mic-toggle` 클릭 핸들러 안에 `ensureAudio();` 한 줄씩 추가(맨 앞).

- [ ] **Step 3: 타입체크 + 무결성 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `grep -c 'playAudio\|class="bubble\|addText\|binaryType' jarvis-web/src/static/home.html` → ≥4
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/home.html
git commit -m "feat(jarvis-web): 홈 채팅 — user/assistant 버블 + TTS Web Audio 재생"
```

---

## Task 5: 통합 검증 + 배포

**Files:** `jarvis-web/scripts/mic_relay_check.mjs`

- [ ] **Step 1: publisher→viewer binary broadcast 검증 추가**

`mic_relay_check.mjs` 의 `main()` 안, 최종 cleanup 전에 추가:
```javascript
  // 8) publisher(=jarvis) 가 보낸 binary(TTS) 를 viewer 가 받는다
  const pub = await open(`${BASE}/publish/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });
  const pubViewer = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  const gotAudio = nextMsg(pubViewer);
  pub.send(Buffer.concat([Buffer.from(Uint32Array.of(16000).buffer), Buffer.from(Int16Array.of(1,2,3).buffer)]));
  const a = await Promise.race([gotAudio, new Promise((_,r)=>setTimeout(()=>r(new Error("timeout")),3000))]).catch((e)=>fail(e.message));
  console.log("publisher→viewer 오디오 binary:", a.isBinary ? "OK" : `FAIL`);
  pub.close(); pubViewer.close();
```
(`nextMsg` 가 `(d, isBinary)` 를 받아 `{isBinary, ...}` 를 돌려주는 기존 헬퍼 형태를 사용 — 파일 내 정의에 맞춰 `a.isBinary` 접근. 다르면 맞춘다.)

- [ ] **Step 2: best-effort 라이브 런** — `.dev.vars`(RELAY_TOKEN=devtoken, ADMIN_PASSWORD=adminpw) 확인 → `cd jarvis-web && npx wrangler dev --port 8787 &>/tmp/jw.log &` (8-10s 대기) → `cd jarvis-web && RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 모든 줄 OK 기대(신규 "publisher→viewer 오디오 binary" 포함). `pkill -f wrangler`. wrangler dev 안 뜨면 스킵+사유 보고.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/scripts/mic_relay_check.mjs
git commit -m "test(jarvis-web): publisher→viewer 오디오 binary 검증"
```

- [ ] **Step 4: 배포 (머지 후 컨트롤러 수행 — 서브에이전트 건너뜀)**
`cd jarvis-web && npm run deploy` (secret 은 이미 설정됨). 스모크: 홈 `/<ROOM_KEY>` 로드.

---

## Self-Review 결과

**Spec coverage:**
- 상시 web_pub + 텍스트 발행 → Task 3 ✓
- emit_audio(binary, sr 헤더) → Task 1 ✓
- TTS 라우팅(remote→웹/로컬 skip, else 로컬) → Task 3 Step3 ✓
- 에코 게이트(web_speaking_until + is_speaking) → Task 3 Step3,4 ✓
- 퍼블리셔 단일화(회의 web_pub 재사용) → Task 3 Step5 ✓
- DO publisher binary → broadcastBinary → Task 2 ✓
- user/assistant kind → Task 2 ✓
- 홈 버블 + Web Audio → Task 4 ✓
- 통합(publisher→viewer binary) → Task 5 ✓
- 연기(C 음성 모드전환) → 미구현 ✓

**Type/이름 consistency:** `emit_audio(pcm_bytes, sr)` 포맷 `struct.pack("<I",sr)+int16` ↔ DO 는 raw broadcast ↔ home `playAudio` 가 `getUint32(0)`+`Int16Array(buf,4)` 로 동일 해석. `web_pub.emit("user"/"assistant", text)` ↔ types `user`/`assistant` ↔ home `addText`. `mic.router.active`(A 에서 추가한 property) 사용.

**핵심 위험:** (1) RelayClient send 루프가 bytes 항목에 `.get("end")` 호출하면 AttributeError → Task1 Step3(d) 에서 isinstance 가드. (2) `MeetingSession.stop` 의 `_relay` None 안전성 → Task3 Step6 확인. (3) AudioContext 자동재생 → 제스처에서 ensureAudio (Task4 Step2d). (4) main.py 에 numpy/time import 누락 → Task3 Step1.
