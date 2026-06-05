# SP2 — 하단 입력 dock + `+` 메뉴 설계

날짜: 2026-06-06

> 큰 묶음("회의 모드 개선") 2번째. SP1(/meet 동기화, 완료) → **SP2(하단 dock)** → SP3(소스 토글)
> → SP4(공개 뷰어). 확정: 소유자는 홈 SPA 안에서 회의(URL 유지).

## 목표

docs/screen.png 하단처럼 음성 버튼을 감싸는 **하단 고정 dock** 을 만든다.
- **홈 뷰**: dock = `[ 🎙 음성 버튼(넓은 pill) ]  [ + ]`. `+` 는 **기능 메뉴** — 탭하면 항목 리스트가
  열린다(현재 항목: **🎤 미팅모드** → 회의 시작). 기능 추가 시 이 메뉴에 항목이 늘어난다.
- **회의 뷰**: `+` 자리에 `[ 🛑 회의 종료 ]`. (SP3 의 소스 토글 버튼은 이후 좌측에 추가.)

## 컴포넌트 변경

### 웹 (`jarvis-web/src/static/app.html`)

- **`#dock`**: 하단 고정 바(`position: fixed; bottom: 0; left/right: 0`), 상단 보더 + `var(--bg)`,
  flex row, 가로 패딩. z-index 15.
- **음성 버튼(`#voice-toggle`)**: 기존 떠 있던 원형을 dock 안 **넓은 pill**(flex:1, 둥근 모서리,
  검은 배경, 파형 SVG + "음성으로 대화" 라벨)로 재스타일. id·클릭 핸들러(핸즈프리 토글)·`active`
  펄스는 그대로. **홈 뷰에서만 표시**.
- **`#dock-plus`**: 우측 원형 `+` 버튼. **홈 뷰에서만 표시**. 클릭 → `#plus-menu` 토글.
- **`#plus-menu`**: dock 위에 뜨는 메뉴 패널(`position: fixed; bottom:<dock 높이>; right`),
  기본 숨김(`.hidden`). 버튼 항목 리스트. 항목:
  - **`#menu-meet`** "🎤 미팅모드" → `sendControl({kind:"meeting_start"})` + 메뉴 닫기.
  - (향후 항목은 여기 추가.)
- **`#meeting-stop`**: 기존 상단 `#controls` 에서 **dock 우측으로 이동**. **회의 뷰에서만 표시**.
  기존 `#controls`(상단 바)와 그 `display:none` 규칙은 제거(meeting-stop 이 dock 으로 감).
- **뷰 게이팅(CSS)**: `body[data-view="home"]` → `#voice-toggle`,`#dock-plus` 표시 / `#meeting-stop`
  숨김. `body[data-view="meeting"]` → 반대. 메뉴는 회의 진입 시 닫힘(JS 에서 view 전환 시/ stop 시 닫기).
- **`#lockbar`**: `bottom` 오프셋을 dock 높이 위로 상향(현재 48px → dock 와 안 겹치게, 예 80px).
- **dock 으로 가려지는 컨텐츠**: `#chat`/`#log` 하단 패딩을 dock 높이만큼 확보(겹침 방지).

### jarvis (`main.py`)

- `_on_remote_command(kind)` 에 분기 추가: `elif kind == "meeting_start": await start_meeting_setup()`.
  (`ControlReceiver` 는 이미 임의 kind 를 일반 포워딩.) 회의 시작 성공 시 SP1 의 `_begin_meeting` 이
  `navigate("meeting")` 발행 → 웹이 회의 뷰로 전환.

## 데이터 흐름

```
[+] → #plus-menu 열림 → [🎤 미팅모드] → sendControl({kind:"meeting_start"})
  → /control → ControlReceiver → _on_remote_command("meeting_start")
  → start_meeting_setup() → _begin_meeting() → navigate("meeting")
  → /subscribe → app.html navigate → showView("meeting") (dock 이 회의 종료 모드로)
[🛑 회의 종료] → sendControl({kind:"meeting_stop"}) (기존) → 홈 복귀
```

## 엣지 케이스

- **메뉴 열린 채 회의 진입**: showView("meeting") 시 메뉴 닫고 dock 을 회의 모드로(CSS + JS 닫기).
- **이미 회의 중 meeting_start**: `start_meeting_setup` 가드가 no-op.
- **control 미연결/RELAY 없음**: sendControl 무시(best-effort), 메뉴는 닫힘.
- **메뉴 바깥 탭**: document 클릭으로 메뉴 닫기(간단한 핸들러).
- **dock 높이 vs lockbar/스크롤**: 패딩·offset 으로 겹침 방지(수동 E2E 확인).

## 테스트 전략

- **단위(jarvis):** `meeting_start` 는 main 클로저 배선 — import/parse 스모크 + 전체 suite 회귀.
  (control 포워딩 자체는 SP 이전 테스트가 커버.)
- **웹:** `app.html` 인라인 JS `node --check`. 라우트 동일성 기존 체크 유지.
- **수동 E2E:** 폰 홈 → 하단 dock 음성 pill 동작(핸즈프리 그대로) → `+` → 메뉴 → 미팅모드 →
  회의 뷰 전환 + jarvis 회의 시작 → dock 에 🛑 회의 종료 → 탭 → 홈 복귀. 콘솔 `/meet` 도 전환(SP1).

## 비범위

- 소스 토글(SP3), 공개 뷰어(SP4). 메뉴의 추가 항목(미래).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/static/app.html` | `#dock` + 음성 pill 재스타일 + `#dock-plus`/`#plus-menu`(미팅모드) + meeting-stop 이동 + lockbar/패딩 조정 + 메뉴/메뉴항목 JS |
| `main.py` | `_on_remote_command` 에 `meeting_start` → `start_meeting_setup()` |
