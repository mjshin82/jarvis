# 회의 종료 동기화 + 웹 종료 버튼 설계

날짜: 2026-06-05

> 상위 비전: jarvis 범용 웹 컨트롤러. 음성 모드 전환(C)·SPA 화 완료 후의 후속 개선.
> 폰에서 회의 모드로 들어간 뒤 발견한 3가지 이슈를 "회의 종료 동기화" 한 묶음으로 해결한다.

## 문제 (3가지)

1. **헤딩 "? ↔ Concode"**: 회의 상대 이름을 받지 않기로 해서 `MeetingMeta.partner_name` 이 항상
   빈 값 → `hello` meta 의 `partner: ""` → 웹 `applyMeta` 가 "?" 로 렌더. 제목을 "🎤 Meeting" 으로.
2. **jarvis `/stop` 시 웹이 모름**: `navigate("home")` 이 음성 의도 경로(`_handle_mode`)에만 있고,
   `/stop` 명령은 `stop_meeting()` 을 직접 호출 → 웹 통지 없음 → 폰이 회의 뷰에 그대로 남음.
3. **웹에 회의 종료 버튼 없음**: 브라우저→jarvis 비오디오 명령 경로가 현재 전무. 웹에서 회의를
   끝내려면 역방향 제어 신호가 필요.

## 목표

회의가 **어느 경로로 끝나든**(음성 "회의 끝내줘" / jarvis `/stop` / 웹 버튼) jarvis 와 웹이
동일 상태로 수렴한다 — jarvis 는 회의 종료, 웹은 홈 뷰. 그리고 웹에서도 회의를 끝낼 수 있다.

## 접근 (역방향 제어 채널)

오늘 브라우저→jarvis 경로는 `/mic`(오디오+JSON) 뿐이다. 마이크 상태와 무관하게 견고하도록
**전용 control 채널**을 마이크 채널과 동일 패턴으로 신설한다(브라우저→jarvis 단방향). 회의 종료의
"단일 진실 지점"은 `stop_meeting()` 으로 두고, 거기서 `navigate("home")` 을 발행한다.

## 컴포넌트 변경

### (1) 헤딩 — web `app.html`

`applyMeta(meta)`: partner 가 있으면 기존 `🎤 ${partner} ↔ ${user}`, 없으면 `🎤 Meeting`.
```js
$("title").textContent = partner ? `🎤 ${partner} ↔ ${user}` : "🎤 Meeting";
```
(`partner` 는 `meta.partner` — 빈 문자열이면 falsy.) meta-badge 의 언어 태그 로직은 유지.

### (2) 종료 동기화 — jarvis `main.py`

`stop_meeting()` 의 **실제 종료 분기**(sess 가 있어 `sess.stop()` 한 경우)의 `finally` 에서
`web_pub.emit("navigate", "home")` 발행(web_pub 있을 때). setup 취소(`cancel_meeting_setup`)
조기 반환과 "회의 모드가 아닙니다"(sess None) 경로에서는 발행하지 않는다(실제 종료가 아님).

`_handle_mode` 의 stop 분기에 있던 `web_pub.emit("navigate", "home")` 은 **제거**(중복 방지).
`_handle_mode` 는 `await stop_meeting()` 만 호출.

결과: 음성 의도·`/stop` 명령·control 채널(아래) 모든 종료가 `stop_meeting` 한 곳을 지나며 통지.

### (3) 전용 control 채널

**relay `src/index.ts`:**
- `forwardToDO` role 유니온에 `"control" | "control-recv"` 추가.
- `GET /control/:key` — `requireAdmin` (브라우저 송신, ADMIN_PASSWORD) → `forwardToDO(..., "control")`.
- `GET /control-recv/:key` — `requireRelayToken` (jarvis 수신, RELAY_TOKEN) → `forwardToDO(..., "control-recv")`.
- 둘 다 `Upgrade: websocket` 아니면 426.

**relay `src/meeting_do.ts`:**
- 필드 `controlSender`, `controlReceiver` (각 `WebSocket | null`).
- `fetch` 의 role 화이트리스트와 분기에 `control`/`control-recv` 추가 → `attachControlSender`/
  `attachControlReceiver`.
- `attachControlSender(ws)`: 기존 sender last-wins(kicked 통지 후 교체). 수신 message(JSON string)를
  `controlReceiver` 로 그대로 `send`. 수신자 없으면 디바운스 `no_receiver` 통지(`attachMicSender` 와 동일).
- `attachControlReceiver(ws)`: 기존 receiver last-wins. 슬롯 저장 + close/error 정리만(상행 메시지 없음).

**jarvis `control_receiver.py` (신규):** `RemoteMicReceiver` 의 JSON 전용 축소판.
- 생성자: `ControlReceiver(url, token, *, on_command, on_log=print, key=None, connect_timeout=5.0)`.
- `{url}/control-recv/{key}` 에 `Authorization: Bearer {token}` 으로 상시 연결, 끊기면 백오프 재연결.
- 수신 루프: JSON 파싱 실패 시 무시. `kind == "meeting_stop"` 이면 `await on_command("meeting_stop")`.
  그 외 kind 는 `on_log` 만(또는 무시).
- `start()`(백그라운드 태스크) / `close()`.

**jarvis `main.py`:**
- 시작부(원격 마이크 수신기 부근)에서 `RELAY_URL && RELAY_TOKEN` 이면
  `control_rx = ControlReceiver(RELAY_URL, RELAY_TOKEN, on_command=_on_remote_command,
  on_log=console.log, key=ROOM_KEY, connect_timeout=RELAY_TIMEOUT_S)` 생성 + `.start()`. 종료 시 close.
- `async def _on_remote_command(kind)`: `if kind == "meeting_stop": await stop_meeting()`.
  (`stop_meeting` 은 정의가 아래지만 호출 시점엔 클로저로 존재 — 기존 `_handle_mode` 와 동일 패턴.)
- RELAY 미설정이면 control_rx 미생성(웹 컨트롤 비활성, 로컬 정상).

**web `app.html`:**
- `#controls` 에 `<button id="meeting-stop">🛑 회의 종료</button>` 추가. CSS: 기본 숨김,
  `body[data-view="meeting"] #meeting-stop { display: inline-block; }` 로 회의 뷰에서만 노출.
- 클릭 핸들러: pw 없으면 무시. `proto://host/control/{name}?token=pw` 로 **one-shot WS**:
  `onopen` 에서 `send(JSON.stringify({kind:"meeting_stop"}))` 후 `close()`. 실패는 조용히 무시.
  뷰 전환은 직접 하지 않음 — jarvis 가 `stop_meeting`→`navigate("home")` 되쏘면 기존 navigate
  핸들러가 `showView("home")`.

## 데이터 흐름 (웹 버튼)

```
[🛑 회의 종료] → ws /control/{key}?token=pw (one-shot)
  → DO.attachControlSender → controlReceiver.send
  → ws /control-recv/{key} → ControlReceiver._handle_message
  → on_command("meeting_stop") → stop_meeting()
      sess.stop(); finally: web_pub.emit("navigate","home")
  → /subscribe → app.html navigate 핸들러 → showView("home")
```

## 엣지 케이스

- **control 수신자 없음(jarvis off)**: DO 가 디바운스 `no_receiver` 를 sender 에 통지(현 mic 과 동일).
  웹 버튼은 무시/조용히 실패. 회의가 실제로 없으니 무해.
- **회의 아닌데 meeting_stop**: `stop_meeting` 이 "회의 모드가 아닙니다" 로그 후 반환, navigate 미발행.
- **RELAY 미설정**: control_rx 미생성. 웹 버튼 비활성, 로컬 회의는 음성/`/stop` 으로 정상 종료.
- **control sender last-wins**: one-shot 이라 충돌 없음. 마이크 슬롯과 별개라 마이크에 영향 없음.
- **종료 시 setup 취소 조기 반환**: navigate 미발행(아직 회의 뷰 전이 전이거나 setup 단계) — 무해.

## 테스트 전략

- **단위(jarvis):** `ControlReceiver._handle_message` — `{"kind":"meeting_stop"}` → 주입한 가짜
  `on_command` 가 `"meeting_stop"` 으로 1회 호출 · 다른 kind/잘못된 JSON → 미호출. (async, ws 불요.)
- **통합(jarvis-web):** `mic_relay_check.mjs` 에 control 검증 추가 — `/control-recv`(RELAY_TOKEN) 수신,
  `/control`(ADMIN) 송신으로 `{kind:"meeting_stop"}` 전송 → 수신측이 그대로 받는지.
- **수동 E2E:** 폰 회의 뷰 → 🛑 회의 종료 → jarvis 회의 종료 + 폰 홈 복귀; jarvis 콘솔 `/stop` →
  폰 홈 복귀; 회의 진입 시 제목 "🎤 Meeting".

## 비범위 / 연기

- control 채널의 다른 명령(모드 전환·음악 등) — 현재 meeting_stop 만. 같은 패턴으로 확장 가능.
- 회의 상대 이름 입력 복원(현 설계상 단일 사용자) — 비범위.
- 웹 버튼의 낙관적 즉시 전환 — jarvis 왕복이 즉각적이라 불필요(일관성 우선).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/static/app.html` | applyMeta 제목 "🎤 Meeting" + `#meeting-stop` 버튼 + one-shot control WS |
| `jarvis-web/src/index.ts` | `/control`·`/control-recv` 라우트 + forwardToDO role |
| `jarvis-web/src/meeting_do.ts` | controlSender/Receiver 슬롯 + attach + forward |
| `control_receiver.py` (신규) | `ControlReceiver` — control-recv 상시 수신, meeting_stop → on_command |
| `main.py` | control_rx 생성·배선, `_on_remote_command`, `stop_meeting` 에 navigate home, `_handle_mode` 중복 제거 |
| `jarvis-web/scripts/mic_relay_check.mjs` | control sender→receiver forward 검증 |
| `tests/` | `ControlReceiver` dispatch 단위 테스트 |
