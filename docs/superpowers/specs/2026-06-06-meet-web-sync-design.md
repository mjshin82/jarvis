# SP1 — /meet ↔ 웹 회의 동기화 설계

날짜: 2026-06-06

> 큰 묶음("회의 모드 개선")의 첫 서브프로젝트. 분해: **SP1(/meet 동기화)** → SP2(하단 입력 박스)
> → SP3(meeting 음성 소스 토글) → SP4(공개 view-only /meeting 뷰어).
> 확정 아키텍처: 소유자는 홈 SPA 안에서 회의(URL /{name} 유지), /{name}/meeting 은 공개 자막 뷰어(SP4).

## 문제

jarvis 가 회의에 진입할 때 `web_pub.emit("navigate","meeting")` 이 **음성/텍스트 의도 경로
(`_handle_mode`, main.py:225)에서만** 발행된다. 콘솔에서 `/meet` 슬래시 명령으로 진입하면
`start_meeting_setup()` 을 직접 호출하므로 웹에 알리지 않아, 웹 SPA 가 회의 뷰로 전환되지 않는다.

(종료는 이미 대칭으로 처리됨 — `stop_meeting()` 내부에서 `navigate("home")` 발행, 모든 종료
경로가 통지.)

## 목표

회의가 **어느 경로로 시작되든**(`/meet` 명령 · 음성 "미팅모드로 변경" · 메타입력) 웹 SPA 가
회의 뷰로 전환된다. 종료(navigate home)와 정확히 대칭.

## 접근

회의 시작의 **단일 지점**은 `_begin_meeting(meta)` 다 — 모든 진입 경로가 이를 거친다
(`start_meeting_setup` 가 `setup.done` 이면 직접 호출(main.py:396), 메타입력 완료 시에도 호출
(main.py:432)). 따라서 `navigate("meeting")` 발행을 `_begin_meeting` 의 **성공 분기**로 옮긴다.

## 변경

**`main.py`:**
- `_begin_meeting()` 의 성공 경로(이미 `web_pub` 가드가 있는 `if web_pub is not None:` 블록 —
  `sess.add_listener(web_pub.emit_async)` 옆)에서 `web_pub.emit("navigate", "meeting")` 발행.
  (회의 시작이 실제로 성공한 뒤에만. 예외 시엔 발행 안 됨.)
- `_handle_mode()` 의 `intent == "meeting"` 분기에 있던 `web_pub.emit("navigate", "meeting")`
  (main.py:223-225) 제거 — `_begin_meeting` 이 책임(중복 방지). `_handle_mode` 는
  `start_meeting_setup()` 호출 + 채팅용 `emit("assistant", "🎤 회의 모드로 전환합니다")` 만 유지.

## 데이터 흐름

```
/meet 명령 → start_meeting_setup() → _begin_meeting() → (성공) web_pub.emit("navigate","meeting")
음성 "미팅모드로 변경" → _handle_mode("meeting") → start_meeting_setup() → _begin_meeting() → 동일
  → /subscribe → app.html navigate 핸들러 → showView("meeting")
```

## 엣지 케이스

- **회의 시작 실패**(예외): navigate 미발행 — 웹은 홈 유지(정상).
- **이미 회의 중 재진입**: `start_meeting_setup` 가드가 막아 `_begin_meeting` 미도달 → 중복 emit 없음.
- **web_pub 미설정(RELAY 없음)**: 가드로 무시, 로컬 회의 정상.
- **SP4(공개 뷰어) 이후**: navigate 이벤트가 공개 뷰어로도 가지 않도록 DO 필터는 SP4 에서 처리.
  SP1 범위에선 기존 broadcast 동작 그대로.

## 테스트 전략

- **단위:** 이 변경은 main 클로저 배선이라 순수 단위 테스트 대상이 아님 — import/parse 스모크 +
  전체 suite 회귀(0 failed).
- **수동 E2E:** 웹 홈 로그인 상태에서 jarvis 콘솔에 `/meet` 입력 → 웹이 회의 뷰로 전환되는지.
  음성 "미팅모드로 변경"도 동일하게 전환. `/stop` → 홈 복귀(기존).

## 비범위

- 공개 뷰어, 하단 UI, 소스 토글 — 각각 SP4/SP2/SP3.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `main.py` | `_begin_meeting` 성공 시 navigate("meeting") 발행, `_handle_mode` 중복 emit 제거 |
