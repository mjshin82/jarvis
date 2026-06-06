# 연결 수명주기 중복 제거 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 복붙된 연결 수명주기 로직을 공유 헬퍼로 추출한다 — Part A: 4개 Python WS 클라이언트의 백오프 루프 → `ws_backoff.reconnect_loop`; Part B: DO 의 5개 단일슬롯 소켓 attach → `attachSlot`.

**Architecture:** 순수 추출, 외부 동작 보존(A 의 로그 문구만 통일). 베이스클래스 대신 합성 함수/private 헬퍼. A 는 pytest 로 회귀망 확보, B 는 typecheck + 수동.

**Tech Stack:** Python 3.11 asyncio + pytest(`.venv/bin/python -m pytest`); TypeScript Cloudflare Worker(`npm run typecheck`).

**스펙:** `docs/superpowers/specs/2026-06-06-connection-lifecycle-dedup-design.md`

---

## Part A — Python WS 백오프 헬퍼

### Task 1: `ws_backoff.py` + 단위테스트 (TDD)

**Files:**
- Create: `ws_backoff.py`
- Test: `tests/test_ws_backoff.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_ws_backoff.py`

```python
import asyncio
from ws_backoff import reconnect_loop


def test_returns_when_stopped_after_success():
    calls = []
    async def run():
        stop = asyncio.Event()
        async def connect_once():
            calls.append(1); stop.set()   # 첫 연결 성공 후 종료 신호
        await reconnect_loop(connect_once, stop, lambda m: None,
                             label="t", init_backoff=0.001, max_backoff=0.002)
    asyncio.run(run())
    assert calls == [1]


def test_retries_and_logs_on_exception():
    calls = []; logs = []
    async def run():
        stop = asyncio.Event()
        async def connect_once():
            calls.append(1)
            if len(calls) >= 3: stop.set()
            raise RuntimeError("boom")
        await reconnect_loop(connect_once, stop, lambda m: logs.append(m),
                             label="t", init_backoff=0.001, max_backoff=0.002)
    asyncio.run(run())
    assert len(calls) == 3
    assert len(logs) == 3
    assert "[t]" in logs[0] and "boom" in logs[0]


def test_cancelled_exits_without_log():
    logs = []
    async def run():
        stop = asyncio.Event()
        async def connect_once():
            raise asyncio.CancelledError()
        await reconnect_loop(connect_once, stop, lambda m: logs.append(m),
                             label="t", init_backoff=0.001, max_backoff=0.002)
    asyncio.run(run())
    assert logs == []


def test_stop_set_before_start_no_connect():
    calls = []
    async def run():
        stop = asyncio.Event(); stop.set()
        async def connect_once(): calls.append(1)
        await reconnect_loop(connect_once, stop, lambda m: None, label="t")
    asyncio.run(run())
    assert calls == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_ws_backoff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ws_backoff'`

- [ ] **Step 3: 구현** — `ws_backoff.py`

```python
# ws_backoff.py
"""WS 클라이언트 공용 재연결 루프.

연결이 끊기면 지수 백오프(init→max)로 재시도하고, stop_event 가 set 되면 종료한다.
relay_client / remote_mic_receiver / control_receiver / gladia_stt 가 공유한다.
"""
import asyncio


async def reconnect_loop(connect_once, stop_event, on_log, *, label,
                         init_backoff=0.5, max_backoff=8.0):
    """connect_once 를 반복 호출. 예외 시 백오프 후 재시도, stop_event 시 정상 종료.

    connect_once: async 콜러블 — 한 번 연결해 끊길 때까지 유지.
    stop_event:   asyncio.Event — set 되면 루프 종료.
    on_log:       (str)->None — 실패 로그 출력.
    label:        로그 출처 식별자(예: "relay").
    """
    backoff = init_backoff
    while not stop_event.is_set():
        try:
            await connect_once()
            backoff = init_backoff          # 성공 → 리셋
        except asyncio.CancelledError:
            return
        except Exception as e:
            on_log(f"[{label}] 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            return                          # stop 신호
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, max_backoff)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_ws_backoff.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add ws_backoff.py tests/test_ws_backoff.py
git commit -m "feat(ws-backoff): 공용 reconnect_loop 헬퍼 + 단위테스트"
```

---

### Task 2: 4개 WS 클라이언트가 `reconnect_loop` 사용

**Files:** Modify `relay_client.py`, `remote_mic_receiver.py`, `control_receiver.py`, `gladia_stt.py`

각 파일에서 ① 상단 import 에 `from ws_backoff import reconnect_loop` 추가, ② `async def _run` 의 인라인 백오프 루프 본문을 한 줄 위임으로 교체. label 은 모듈별로 지정.

- [ ] **Step 1: relay_client.py**

상단 import 블록(다른 `import`/`from` 들 근처)에 `from ws_backoff import reconnect_loop` 추가.
현재 `_run`(약 132~150):
```python
    async def _run(self) -> None:
        """sender 메인 루프. 끊기면 백오프 후 재연결. _stop 신호 받으면 종료."""
        backoff = 0.5
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 0.5   # 성공했으니 리셋
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_log(f"[relay] 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return   # stop signal
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)
```
교체 후:
```python
    async def _run(self) -> None:
        """sender 메인 루프. 끊기면 백오프 재연결, _stop 시 종료(공용 헬퍼)."""
        await reconnect_loop(self._connect_once, self._stop, self.on_log, label="relay")
```

- [ ] **Step 2: control_receiver.py**

상단에 `from ws_backoff import reconnect_loop` 추가. 현재 `_run`(약 66~83, relay 와 동일 구조, 로그 `[control] 수신 연결 끊김/실패`)의 본문을 교체:
```python
    async def _run(self):
        await reconnect_loop(self._connect_once, self._stop, self.on_log, label="control")
```

- [ ] **Step 3: gladia_stt.py**

상단에 `from ws_backoff import reconnect_loop` 추가. 현재 `_run`(약 96~113, 로그 `[gladia] 끊김/실패`)의 본문을 교체:
```python
    async def _run(self):
        await reconnect_loop(self._connect_once, self._stop, self.on_log, label="gladia")
```

- [ ] **Step 4: remote_mic_receiver.py**

상단에 `from ws_backoff import reconnect_loop` 추가. 현재 `_run`(약 84~101, 로그 `[mic] 수신 연결 끊김/실패`)의 본문을 교체:
```python
    async def _run(self):
        await reconnect_loop(self._connect_once, self._stop, self.on_log, label="mic")
```

> 주의: 각 모듈의 `_connect_once`, `self._stop`(asyncio.Event), `self.on_log` 는 이미 존재한다.
> `asyncio` import 가 다른 곳에서 여전히 쓰이면 남기고, 더 이상 안 쓰이면 그대로 둬도 무방(불필요 제거는 선택).

- [ ] **Step 5: import 확인 + 전체 테스트**

Run:
```bash
.venv/bin/python -c "import relay_client, remote_mic_receiver, control_receiver, gladia_stt; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, **99 passed** (기존 95 + ws_backoff 4).

- [ ] **Step 6: 커밋**

```bash
git add relay_client.py remote_mic_receiver.py control_receiver.py gladia_stt.py
git commit -m "refactor(ws): 4개 WS 클라이언트가 공용 reconnect_loop 사용"
```

---

## Part B — 웹 DO 단일슬롯 소켓 헬퍼

### Task 3: `attachSlot` 헬퍼 + 5개 attach* 전환

**Files:** Modify `jarvis-web/src/meeting_do.ts`

**검증:** `cd jarvis-web && npm run typecheck`(에러 0). DO 테스트 하니스 없음 → 동작은 수동 E2E(Task 4).

- [ ] **Step 1: `attachSlot` private 헬퍼 추가**

`// --- helpers ---` 주석 아래(`buildEvent` 정의 앞)에 추가:
```typescript
  // 단일슬롯 소켓(publisher/micSender/micReceiver/controlSender/controlReceiver) 공통 부착.
  // 기존 슬롯이 있으면 (선택)kick 통지 후 close, 새 ws 를 슬롯에 대입, 핸들러 등록.
  // close/error 시 현재 슬롯이 이 ws 면 비운다(+ 선택 onClose).
  private attachSlot(
    ws: WebSocket,
    get: () => WebSocket | null,
    set: (v: WebSocket | null) => void,
    opts: {
      kick?: boolean;
      onMessage?: (data: string | ArrayBuffer) => void;
      onClose?: () => void;
    },
  ): void {
    const cur = get();
    if (cur) {
      try {
        if (opts.kick) this.safeSend(cur, this.buildEvent({ kind: "kicked", reason: "replaced" }));
        cur.close(1000, "replaced");
      } catch { /* */ }
    }
    set(ws);
    if (opts.onMessage) {
      ws.addEventListener("message", (m) => opts.onMessage!((m as MessageEvent).data as string | ArrayBuffer));
    }
    ws.addEventListener("close", () => { if (get() === ws) { set(null); opts.onClose?.(); } });
    ws.addEventListener("error", () => { if (get() === ws) set(null); });
  }
```

- [ ] **Step 2: `attachPublisher` 전환**

현재(약 82~122)를 교체:
```typescript
  private attachPublisher(ws: WebSocket): void {
    this.attachSlot(ws, () => this.publisher, (v) => (this.publisher = v), {
      kick: true,
      onMessage: (data) => {
        if (data instanceof ArrayBuffer) { this.broadcastBinary(data); return; }
        let parsed: ClientMessage | null = null;
        try { parsed = JSON.parse(data as string) as ClientMessage; } catch { return; }
        this.handlePublisherMessage(parsed);
      },
      onClose: () => this.broadcast(this.buildEvent({ kind: "publisher_disconnected" })),
    });
    this.notifyViewerCount();   // 재연결 publisher 에게 현재 owner 수 동기화
  }
```

- [ ] **Step 3: `attachMicSender` 전환**

현재(약 197~223)를 교체 (no_receiver 응답은 outer `ws` 클로저 사용):
```typescript
  private attachMicSender(ws: WebSocket): void {
    this.attachSlot(ws, () => this.micSender, (v) => (this.micSender = v), {
      kick: true,
      onMessage: (data) => {
        if (!this.micReceiver) {
          const now = Date.now();
          if (now - this.lastNoReceiverAt > 2000) {   // 프레임마다 통지하지 않도록 디바운스
            this.lastNoReceiverAt = now;
            this.safeSend(ws, { ts: now / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
          }
          return;
        }
        try { this.micReceiver.send(data); } catch { /* 수신측 끊김 — 다음 close 에서 정리 */ }
      },
    });
  }
```

- [ ] **Step 4: `attachMicReceiver` 전환**

현재(약 225~244)를 교체:
```typescript
  private attachMicReceiver(ws: WebSocket): void {
    this.attachSlot(ws, () => this.micReceiver, (v) => (this.micReceiver = v), {
      onMessage: (data) => {
        let parsed: any = null;
        try {
          const raw = typeof data === "string" ? data : new TextDecoder().decode(data as ArrayBuffer);
          parsed = JSON.parse(raw);
        } catch { return; }
        if (parsed && parsed.kind === "mic_source") {
          this.lastMicSource = parsed.source ?? null;
          this.broadcast(this.buildEvent({ kind: "mic_source", source: parsed.source }));
        }
      },
    });
  }
```

- [ ] **Step 5: `attachControlSender` 전환**

현재(약 246~268)를 교체 (no_receiver 응답은 outer `ws`):
```typescript
  private attachControlSender(ws: WebSocket): void {
    this.attachSlot(ws, () => this.controlSender, (v) => (this.controlSender = v), {
      kick: true,
      onMessage: (data) => {
        if (!this.controlReceiver) {
          const now = Date.now();
          if (now - this.lastControlNoReceiverAt > 2000) {
            this.lastControlNoReceiverAt = now;
            this.safeSend(ws, { ts: now / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
          }
          return;
        }
        try { this.controlReceiver.send(data); } catch { /* 수신측 끊김 */ }
      },
    });
  }
```

- [ ] **Step 6: `attachControlReceiver` 전환**

현재(약 270~277)를 교체 (kick 없음, onMessage 없음 — 순수 파이프 대상):
```typescript
  private attachControlReceiver(ws: WebSocket): void {
    this.attachSlot(ws, () => this.controlReceiver, (v) => (this.controlReceiver = v), {});
  }
```

> `attachViewer` 는 변경하지 않는다(Map 기반 다중 뷰어). `MeetingDO` export 유지.

- [ ] **Step 7: 타입체크**

Run: `cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck`
Expected: 에러 없음(종료코드 0).

- [ ] **Step 8: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/meeting_do.ts
git commit -m "refactor(do): 단일슬롯 소켓 attach 를 attachSlot 헬퍼로 통합"
```

---

## Task 4: 최종 검증

**Files:** (변경 없음)

- [ ] **Step 1: Python**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -c "import main; print('import ok')"`
Expected: 99 passed, `import ok`.

- [ ] **Step 2: Web 타입체크**

Run: `cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck`
Expected: 에러 없음.

- [ ] **Step 3: 수동 E2E (배포 후, 사용자)**
- A(jarvis 재시작): 웹 연결(relay)·폰 mic 수신(mic)·웹 제어 명령(control)·회의 STT(gladia) 가 정상 연결되고, 끊었다 켜면 재연결되는지(로그 `[relay]`/`[mic]`/`[control]`/`[gladia]`).
- B(`wrangler deploy` 후): 회의 입·퇴장(publisher), 폰 mic+소스 토글(micSender/Receiver), 2기기 mic 경합(kick), 웹 제어 명령(controlSender/Receiver), 공개 뷰어 자막.

---

## 비고
- 순수 추출 — A 의 로그 문구 통일(`[label] 연결 끊김/실패: …`) 외 외부 동작 보존.
- A 는 jarvis 재시작, B 는 `wrangler deploy`(사용자 확인 후). origin push 는 사용자가 직접.
- `MeetingDO` export·`attachViewer`·재연결 정책 수치는 불변.
