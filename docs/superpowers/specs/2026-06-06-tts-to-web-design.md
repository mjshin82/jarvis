# TTS 출력을 웹 클라이언트로 (presence 기반) 설계

날짜: 2026-06-06

## 목표

웹 클라이언트(홈 SPA = `/subscribe` owner 뷰어)가 1개 이상 붙어 있으면, jarvis 의 음성(TTS)
출력을 **항상 웹으로**(접속한 모든 owner 클라이언트로) 보낸다. 웹 클라이언트가 없으면 기존대로
로컬 스피커로 재생.

(현재는 폰 마이크(remote)가 active 일 때만 웹으로 — 시스템 마이크 사용 중엔 웹 뷰어가 있어도
로컬로만 나갔다.)

## 정의

- **"웹 클라이언트"** = `/subscribe` 로 붙은 **owner** 뷰어. 공개 `/watch`(public) 뷰어는 TTS 오디오를
  애초에 못 받으므로(SP4 필터) presence 집계에서 제외.
- 다중 클라이언트 전송은 자동 — DO `broadcastBinary` 가 이미 모든 owner 뷰어에 fan-out.

## 접근

jarvis 가 "owner 뷰어 수"를 알아야 라우팅을 결정한다. DO 가 viewer 접속/해제마다 **publisher
(jarvis web_pub)에게 viewer 수를 통지**하고, `RelayClient` 가 이를 수신해 `web_viewer_count` 로 보관.
`speak_response` 가 그 값으로 웹/로컬을 결정.

## 컴포넌트 변경

### relay 타입 (`jarvis-web/src/types.ts`)

- `EventKind` 에 `"viewers"` 추가.
- `ClientMessage` 에 `count?: number` 추가.

### DO (`jarvis-web/src/meeting_do.ts`)

- 헬퍼 `notifyViewerCount()`: `this.publisher` 가 있으면 owner 뷰어 수를 세어
  `this.safeSend(this.publisher, this.buildEvent({ kind: "viewers", count }))`.
- `attachViewer`: `this.viewers.set(ws, role)` 직후 + close/error 핸들러의 `delete` 직후 호출.
- `attachPublisher`: `this.publisher = ws` 직후 호출(퍼블리셔 재연결 시 현재 수 1회 전송).
- `viewers` 메시지는 **publisher 전용**(broadcast 경로 안 탐) — viewer 필터/replay 무관.

### RelayClient (`relay_client.py`)

- 생성자: `self.web_viewer_count = 0`.
- `_connect_once`: send-only → **send/recv 동시**. 기존 큐소비 루프를 `_send_loop(ws)` 로 추출,
  새 `_recv_loop(ws)` 추가. `asyncio.wait({send, recv}, FIRST_COMPLETED)` 후 정리. 끊기면
  `web_viewer_count = 0`.
- `_recv_loop`: `async for raw in ws` → `_handle_inbound(raw)`.
- `_handle_inbound(raw)`: bytes 무시, JSON 파싱 실패 무시, `kind=="viewers"` 면
  `self.web_viewer_count = int(count or 0)`. (테스트 용이성 위해 분리.)

### main.py (`speak_response`)

- 현재: `if web_pub is not None and mic.router.active == "remote":`
- 변경: `if web_pub is not None and web_pub.web_viewer_count > 0:`
- 웹 분기(emit_audio + web_speaking_until 갱신)·로컬 폴백(player.enqueue) 그대로.

## 데이터 흐름

```
웹 홈 열림/닫힘 → DO attachViewer.set / close.delete → notifyViewerCount()
  → publisher 로 {kind:"viewers", count} → RelayClient._recv_loop → web_viewer_count 갱신
speak_response: web_viewer_count>0 ? emit_audio(→broadcastBinary→모든 owner 뷰어) : player.enqueue
```

## 엣지 케이스

- **웹 뷰어 0**: 로컬 스피커 폴백(현행).
- **퍼블리셔(jarvis) 재연결**: attachPublisher 가 현재 owner 수 재전송 → web_viewer_count 복구.
  RelayClient 가 끊기면 web_viewer_count=0 → 재연결까지 로컬(보수적).
- **web_pub None(RELAY 미설정)**: web_viewer_count 속성 기본 0 → 항상 로컬.
- **시스템 마이크 + 웹 TTS**: TTS 는 폰 브라우저에서 재생되어 시스템 마이크에 에코 없음.
  web_speaking_until 게이트는 유지(무해 — TTS 추정 시간 동안 VAD 억제).
- **public(/watch) 뷰어만 있음**: owner 수 0 → 로컬. (공개 뷰어는 오디오 못 받음.)
- **다중 owner**: broadcastBinary 가 전원에 전송(자동).

## 테스트 전략

- **단위(jarvis):** `RelayClient._handle_inbound` — `{kind:"viewers",count:N}` → web_viewer_count=N,
  count 0/누락/비JSON/bytes 안전. (ws 불요.)
- **통합(`mic_relay_check.mjs`):** publisher 연결 후 `/subscribe` viewer 접속 → publisher 가
  `{kind:"viewers", count>=1}` 수신.
- **수동 E2E:** 폰 홈(웹) 열어둔 채 jarvis 가 **시스템 마이크**로 대화 → TTS 가 폰에서 재생(로컬 X).
  폰 홈 닫으면 → 로컬 스피커로. 두 기기 홈 열면 둘 다 재생.

## 비범위

- 클라이언트별 개별 음소거/볼륨. 로컬+웹 동시 재생(현재는 웹 있으면 웹만).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/types.ts` | `viewers` kind + `count?` |
| `jarvis-web/src/meeting_do.ts` | `notifyViewerCount` + attachViewer/attachPublisher 통지 |
| `relay_client.py` | `web_viewer_count` + recv 루프(`_recv_loop`/`_handle_inbound`) |
| `main.py` | speak_response 라우팅 조건 변경 |
| `tests/test_relay_client.py` (신규) | `_handle_inbound` 단위 |
| `jarvis-web/scripts/mic_relay_check.mjs` | viewer presence 통지 검증 |
