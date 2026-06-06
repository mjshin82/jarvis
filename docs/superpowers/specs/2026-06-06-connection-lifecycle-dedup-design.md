# 연결 수명주기 중복 제거 설계

날짜: 2026-06-06

## 목표

복붙된 "연결 수명주기" 로직 두 곳을 공유 헬퍼로 추출한다.
- **Part A (Python)**: 4개 WS 클라이언트의 동일한 백오프/재연결 루프 → `ws_backoff.reconnect_loop`.
- **Part B (web)**: Durable Object 의 5개 단일슬롯 소켓 attach 패턴 → `attachSlot` private 헬퍼.

성격: **순수 추출, 외부 동작 보존**(A 의 로그 문구만 통일). 버그수정 지점이 N→1 로 줄고
향후 버그 표면이 감소한다.

## 비범위 (YAGNI)
- 베이스클래스/상속 도입 안 함(합성 함수/헬퍼로 충분).
- `attachViewer`(Map 기반 다중 뷰어)는 단일슬롯이 아니라 제외.
- 재연결 정책 변경(백오프 수치/상한) 없음 — 기존 값(0.5s→8s) 유지.
- 새 의존성(zod 등) 없음.

---

## Part A — Python WS 백오프 헬퍼

### 현재 (중복)
`relay_client.py:132-150`, `remote_mic_receiver.py:84-101`, `control_receiver.py:66-83`,
`gladia_stt.py:96-113` 의 `_run` 이 **구조적으로 동일**:
```
backoff=0.5
while not _stop.is_set():
    try: await _connect_once(); backoff=0.5
    except CancelledError: return
    except Exception as e: on_log(f"[<prefix>] ...: {e} — {backoff:.1f}s 후 재시도")
    if _stop.is_set(): return
    try: await wait_for(_stop.wait(), timeout=backoff); return
    except TimeoutError: pass
    backoff = min(backoff*2, 8.0)
```
차이는 로그 접두사/문구뿐(`[relay] 연결 끊김/실패` / `[control] 수신 연결 끊김/실패` /
`[gladia] 끊김/실패` / `[mic] 수신 연결 끊김/실패`).

### 신규 `ws_backoff.py`
```python
# ws_backoff.py
"""WS 클라이언트 공용 재연결 루프 — 끊기면 지수 백오프로 재시도, _stop 시 종료."""
import asyncio


async def reconnect_loop(connect_once, stop_event, on_log, *, label,
                         init_backoff=0.5, max_backoff=8.0):
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

### 적용
각 모듈의 `_run` 본문을 다음으로 교체(시그니처/호출부 불변):
```python
async def _run(self):
    await reconnect_loop(self._connect_once, self._stop, self.on_log, label="relay")
```
label: `relay_client`→`"relay"`, `remote_mic_receiver`→`"mic"`, `control_receiver`→`"control"`,
`gladia_stt`→`"gladia"`. `_connect_once`/`_stop`/`on_log` 는 각 모듈에 이미 존재.

- **의도된 변경**: 로그 문구가 `[label] 연결 끊김/실패: {e} — {backoff}s 후 재시도` 로 통일.
- **불변**: 백오프 동작, CancelledError·TimeoutError 처리, stop 종료 의미 동일.

### 테스트 (`tests/test_ws_backoff.py`, 신규)
fake `connect_once` + `asyncio.Event` 로:
- 성공 후 다시 호출되면 backoff 리셋(연속 실패 시 증가, 성공 시 0.5 복귀) 관찰 가능하도록 `connect_once` 가 호출횟수/대기 기록.
- 예외 발생 시 on_log 호출 + 재시도(다음 루프 진입) 확인.
- 루프 시작 전/중 `stop_event.set()` → 즉시/다음 사이클에 return.
- `connect_once` 가 `CancelledError` raise → 루프 return(로그 없음).
- 단순화를 위해 `max_backoff`/`init_backoff` 를 작게(예: 0.01) 주입해 빠르게.

---

## Part B — 웹 DO 단일슬롯 소켓 헬퍼

### 현재 (중복)
`meeting_do.ts` 의 `attachPublisher`(82-122), `attachMicSender`(197-223),
`attachMicReceiver`(225-244), `attachControlSender`(246-268), `attachControlReceiver`(270-277)
가 동일 골격 반복: 기존 슬롯 있으면 (선택)kick + close → 슬롯에 새 ws 대입 →
message 핸들러 등록 → close/error 에서 `if (slot === ws) slot = null` (+ 일부 부가효과).

### 신규 private 헬퍼 `attachSlot`
```typescript
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
    ws.addEventListener("message", (m) => opts.onMessage!((m as MessageEvent).data));
  }
  ws.addEventListener("close", () => { if (get() === ws) { set(null); opts.onClose?.(); } });
  ws.addEventListener("error", () => { if (get() === ws) set(null); });
}
```

### 적용 (동작 보존)
- `attachPublisher`: `attachSlot(ws, ()=>this.publisher, v=>this.publisher=v, { kick:true,
  onMessage:(data)=>{ if (data instanceof ArrayBuffer) this.broadcastBinary(data); else { parse → this.handlePublisherMessage } },
  onClose:()=>this.broadcast(this.buildEvent({kind:"publisher_disconnected"})) })`,
  그리고 attachSlot 호출 **후** `this.notifyViewerCount()` (재연결 publisher 에 현재 수 동기화).
- `attachMicSender`: `kick:true`, onMessage = 기존 forward(수신자 없으면 no_receiver 디바운스).
- `attachMicReceiver`: kick 없음, onMessage = mic_source 파싱 → broadcast.
- `attachControlSender`: `kick:true`, onMessage = 기존 forward(수신자 없으면 no_receiver 디바운스).
- `attachControlReceiver`: kick 없음, onMessage = 기존 forward.
- `attachViewer`: **변경 없음**(Map 기반 다중 뷰어 — 단일슬롯 아님).

기존 각 메서드의 message 핸들러 본문(데이터 파싱/포워딩/디바운스)은 그대로 `onMessage` 콜백으로 옮긴다. `MeetingDO` export 유지(DO 바인딩 필수).

### 검증
DO 테스트 하니스 없음 → `cd jarvis-web && npm run typecheck`(에러 0) + 수동 E2E
(배포 후): 회의 입·퇴장(publisher kick/disconnect), 폰 mic 입력+소스 토글(micSender/Receiver),
2기기 mic 경합(kick), 웹 제어 명령(controlSender/Receiver), 공개 뷰어 자막(viewer/watch).

---

## 데이터 흐름 / 동작
변경 없음 — 추출만. A 는 `_run`→`reconnect_loop` 위임, B 는 `attach*`→`attachSlot` 위임.

## 엣지
- A: `init_backoff`/`max_backoff` 는 기본값으로 기존과 동일. label 만 모듈별.
- B: kick 여부·onClose 부가효과·notifyViewerCount 타이밍을 정확히 보존(특히 publisher).
  `safeSend`/`buildEvent`/`broadcast`/`broadcastBinary`/`handlePublisherMessage` 는 기존 메서드 재사용.

## 테스트 / 회귀
- A: pytest 기존 95 + `test_ws_backoff.py` 신규. import 확인.
- B: typecheck 0 + 수동 E2E. 파이썬 테스트 영향 없음.

## 영향 파일
| 파일 | 변경 |
|------|------|
| `ws_backoff.py` (신규) | `reconnect_loop` |
| `relay_client.py`, `remote_mic_receiver.py`, `control_receiver.py`, `gladia_stt.py` | `_run` → `reconnect_loop` 위임 |
| `tests/test_ws_backoff.py` (신규) | reconnect_loop 단위테스트 |
| `jarvis-web/src/meeting_do.ts` | `attachSlot` 추가, 5개 attach* 가 사용 |

배포: A 는 jarvis 재시작, B 는 `wrangler deploy`(사용자 확인 후). origin push 는 사용자가 직접.
