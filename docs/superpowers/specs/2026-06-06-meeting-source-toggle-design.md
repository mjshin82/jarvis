# SP3 — meeting 음성 소스 토글 설계

날짜: 2026-06-06

> 큰 묶음 3번째. SP1(/meet 동기화)·SP2(하단 dock) 완료 → **SP3(소스 토글)** → SP4(공개 뷰어).

## 목표

회의 모드에서 음성 입력을 **jarvis 본체(시스템 마이크)** 로 받을지 **웹(폰 마이크)** 으로 받을지
dock 에서 선택. 일반 모드의 음성 pill 자리에 소스 토글 pill 을 둔다.

## 접근

이미 있는 `MicRouter.set_override("local"|"remote")`(콘솔 `/mic system|phone` 과 동일, auto 해제 +
강제 고정)를 control 채널로 호출. 소스 변경은 기존 `on_switch` → `mic_source` 이벤트로 웹에 반영.
control 명령은 payload 없이 **kind 로 구분**(현 `_on_remote_command(kind)` 시그니처 유지).

## 컴포넌트 변경

### 웹 (`jarvis-web/src/static/app.html`)

- **`#mic-src-toggle`**(신규 버튼, 회의 전용, dock 좌측 flex:1 pill — 음성 pill 과 동일 스타일):
  현재 소스 표시 "🎙 입력: 시스템" / "🎙 입력: 폰". 탭하면 반대 소스로 전환 요청.
- dock 레이아웃(회의): `[ #mic-src-toggle (flex:1) ]  [ #meeting-stop (auto) ]`.
  `#meeting-stop` 의 `flex:1` 제거(auto 폭, 우측). 홈은 기존대로 voice pill + `+`.
- CSS 게이팅: `body[data-view="home"] #mic-src-toggle{display:none}`,
  `body[data-view="meeting"]` 에서 표시. (홈에서 숨김.)
- 상태/이벤트:
  - `let micSource = "system"` (추적). 라벨: `remote`→"🎙 입력: 폰", 그 외→"🎙 입력: 시스템".
  - `handle()` 의 `mic_source` case 를 **무시 → 라벨 갱신**으로 변경: `micSource = ev.source;`
    `#mic-src-toggle` 텍스트 갱신. (헤더 배지는 부활 안 함 — dock 토글만.)
  - 클릭: `micSource === "remote"` 면 `sendControl({kind:"mic_system"})`, 아니면
    `sendControl({kind:"mic_phone"})`. 실제 전환은 jarvis 의 mic_source 이벤트가 돌아와 라벨 확정.
  - DO 가 새 viewer 에 `lastMicSource` 재생 → 회의 진입/재접속 시 초기 라벨 동기화.

### jarvis (`main.py`)

- `_on_remote_command(kind)` 분기 추가:
  - `"mic_system"` → `mic.router.set_override("local")`
  - `"mic_phone"` → `mic.router.set_override("remote")`
  (`mic.router` 는 항상 존재. set_override 가 on_switch→notify_source→mic_source 이벤트 발행.)

## 데이터 흐름

```
[🎙 입력 토글] 탭 → sendControl({kind:"mic_system"|"mic_phone"})
  → /control → ControlReceiver → _on_remote_command → mic.router.set_override("local"|"remote")
  → (소스 변경 시) on_switch → remote_mic_rx.notify_source → mic_source 이벤트
  → /subscribe → app.html handle("mic_source") → micSource 갱신 + 토글 라벨 갱신
```

## 엣지 케이스

- **본체 선택했는데 폰만 말함(또는 반대)**: 선택한 소스만 STT 에 먹음 — 의도된 동작. set_override 가
  auto 자동전환을 끔.
- **REMOTE_MIC 비활성 / RELAY 없음**: set_override 는 안전(no-op 수준). 웹 control 미전달 시 토글 무반응(best-effort).
- **회의 아님인데 토글**: 토글은 회의 뷰에서만 보임 → 발생 안 함. (만약 명령이 와도 set_override 는
  소스만 바꿈, 무해.)
- **초기 라벨**: mic_source 이벤트 도착 전엔 기본 "시스템" 표시, 도착 시 정정.

## 테스트 전략

- **단위(jarvis):** mic_system/mic_phone 은 main 클로저 배선 — import/parse 스모크 + 전체 suite 회귀.
- **웹:** `app.html` 인라인 JS `node --check`.
- **수동 E2E:** 회의 진입 → dock 좌측 "🎙 입력: 시스템" 표시 → 탭 → "폰" 으로 바뀌고 jarvis 가 폰
  마이크 입력 사용(콘솔 로그 "입력 소스 → 원격(폰)") → 다시 탭 → 시스템 복귀. 회의 종료 버튼 정상.

## 비범위

- 공개 뷰어(SP4). 소스 자동(auto) 선택 UI(콘솔 `/mic auto` 로만).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/static/app.html` | `#mic-src-toggle` 버튼 + CSS 게이팅 + `mic_source` 핸들러(라벨) + 클릭 + meeting-stop 폭 |
| `main.py` | `_on_remote_command` 에 `mic_system`/`mic_phone` → `set_override` |
