# 핸즈프리 음성 버튼 설계

날짜: 2026-06-06

> 상위 비전: jarvis 범용 웹 컨트롤러. 홈 채팅에 ChatGPT 스타일 음성 입력 버튼을 추가해,
> 탭 한 번으로 폰 마이크를 켜고 호출어 없이 바로 청취(타임아웃 없음)에 들어간다.

## 목표

웹 홈 채팅 **우하단**에 원형 음성 버튼(파형 아이콘)을 둔다.
- **토글 ON**: 폰 마이크 캡처 ON + "Hey Jarvis" 가 불린 것처럼 **즉시 청취 모드** 진입.
- 이렇게 진입한 청취는 **무발화 타임아웃이 없다**(계속 듣는다).
- **토글 OFF**: 청취 중단 + 마이크 OFF → 원래대로(호출어 대기) 복귀.

## 접근

이전에 만든 브라우저→jarvis 전용 control 채널(`/control` + `/control-recv` + `ControlReceiver`)을
재사용한다. 버튼은 `{kind:"listen_start"}` / `{kind:"listen_stop"}` 를 보내고, jarvis 는
`hands_free` 플래그로 청취 타임아웃을 무효화한다. "고 프리 보이스" — 버튼이 마이크 캡처까지 켠다.

## 컴포넌트 변경

### (1) 웹 — 음성 토글 버튼 (`jarvis-web/src/static/app.html`)

- `#voice-toggle` 버튼: 홈 채팅 **우하단 고정**(`position: fixed; right/bottom`), 검은 원 + 파형 SVG
  (ChatGPT 음성 버튼 스타일). 회의 뷰에선 숨김(`body[data-view="meeting"] #voice-toggle{display:none}`).
  활성 시 빨강/펄스 표시.
- `sendControl(obj)` 헬퍼 추출: `/control/{name}?token=pw` 로 one-shot WS — open→`send(JSON)`→close.
  기존 meeting-stop 버튼의 인라인 제어 WS 를 이 헬퍼로 교체(DRY).
- 클릭 핸들러(`voiceOn` 토글):
  - **ON**: `ensureAudio()`; `micOn` 이 false 면 `await micStart()` + `micOn=true` + `#mic-toggle` 라벨/
    스타일 동기화; `sendControl({kind:"listen_start"})`; 버튼 활성 클래스.
  - **OFF**: `sendControl({kind:"listen_stop"})`; 이 버튼이 켠 마이크면 `micStop()` + `micOn=false` +
    `#mic-toggle` 동기화; 버튼 비활성.
- 기존 `#mic-toggle`(수동 스트리밍) 버튼은 유지. 둘은 `micOn`/`micStart`/`micStop` 을 공유해 상태 일관.

### (2) jarvis — 핸즈프리 청취 (`main.py`)

- nonlocal `hands_free = False`.
- `_on_remote_command(kind)` 에 분기 추가:
  - `"listen_start"`: `hands_free = True` → `await trigger_wake()` (호출어 없이 즉시 청취).
  - `"listen_stop"`: `hands_free = False` → 진행 중 응답 취소(`await cancel(response); response=None`) →
    `idle()` (WAITING_WAKE 복귀 = 원래대로).
- `listen_timeout`: 번역 모드 체크 다음에 `if hands_free: return` → **타임아웃 없음**.
- 응답 후 재청취 규칙(두 곳: `_respond_voice`, `respond_flow_audio` 꼬리):
  `if hands_free or config.FOLLOW_UP:` 면 `enter_listening(cue=True)`, 아니면 `idle()`.
  → 핸즈프리면 FOLLOW_UP 설정과 무관하게 계속 듣는다.

### (3) 제어 채널 일반화 (`control_receiver.py`)

- `_handle_message`: `kind == "no_receiver"` 는 로그, **그 외 유효한 kind 는 `await on_command(kind)`**
  로 포워딩(meeting_stop·listen_start·listen_stop 및 향후 확장). main 의 `_on_remote_command` 가
  알 수 없는 kind 는 무시.
- 기존 단위 테스트(`test_other_kinds_ignored`)를 새 동작에 맞게 갱신: 임의 kind 도 on_command 호출,
  `no_receiver`/빈 kind/잘못된 JSON 은 미호출.

## 데이터 흐름

```
[●파형 버튼 ON]
  micStart() (폰 마이크 → /mic → jarvis)
  sendControl({kind:"listen_start"})
    → /control → DO → /control-recv → ControlReceiver → on_command("listen_start")
    → hands_free=True; trigger_wake() → enter_listening (탭=recognizer, listen_timeout 무효)
  말하면 partial(조합중)→응답, 응답 후 hands_free 라 계속 재청취
[버튼 OFF]
  sendControl({kind:"listen_stop"}); micStop()
    → on_command("listen_stop") → hands_free=False; 응답 취소; idle() → WAITING_WAKE
```

## 엣지 케이스

- **control 미연결/RELAY 미설정**: sendControl WS 실패는 조용히 무시(마이크만 켜짐). jarvis control_rx
  없으면 listen_start 미수신 — best-effort.
- **토글 OFF 가 응답 도중**: listen_stop 이 response 를 취소하고 idle → 깔끔히 정지(명시적 중단이라 허용).
- **listen_start 중복**(이미 청취 중): `trigger_wake` 가 재진입(무해).
- **페이지 리로드**: 버튼 상태 OFF 로 초기화. jarvis 가 핸즈프리로 남아도 마이크 끊겨 입력 없음(무해).
  unload 시 listen_stop 자동 통지는 비범위.
- **recognizer 없음(폴백)**: VAD 경로로도 핸즈프리 동작(listen_timeout 만 무효).
- **회의 모드와 동시**: 비범위(상호 배타 가정).

## 테스트 전략

- **단위(jarvis):** `ControlReceiver._handle_message` — `listen_start`/`listen_stop`/`meeting_stop` →
  `on_command` 1회 호출(해당 kind), `no_receiver`/빈 kind/비 JSON → 미호출. (기존 테스트 갱신.)
- **웹:** `app.html` 인라인 JS `node --check`.
- **수동 E2E:** 폰 홈 → 우하단 파형 버튼 탭 → 마이크 ON + jarvis 가 호출어 없이 청취(타임아웃 없음) →
  말하면 조합중→응답, 가만 둬도 대기 유지 → 다시 탭 → 정지 + 마이크 OFF + 호출어 대기 복귀.

## 비범위 / 연기

- 페이지 unload 시 자동 listen_stop 통지.
- 핸즈프리 + 회의 모드 동시 운용.
- 버튼/마이크-토글의 정교한 상태 머신(현재는 micOn 공유로 단순 동기화).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/static/app.html` | `#voice-toggle` 버튼 + `sendControl` 헬퍼 + 마이크 결합 토글; meeting-stop 을 헬퍼로 교체 |
| `main.py` | `hands_free` 플래그, `_on_remote_command` listen_start/stop, listen_timeout 무효, 재청취 규칙 |
| `control_receiver.py` | `_handle_message` 일반 포워딩 |
| `tests/test_control_receiver.py` | 일반 포워딩 동작에 맞게 갱신 |
