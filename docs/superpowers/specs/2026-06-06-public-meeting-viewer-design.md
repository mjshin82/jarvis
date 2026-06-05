# SP4 — 공개 view-only /meeting 뷰어 설계

날짜: 2026-06-06

> 큰 묶음 마지막. SP1(/meet 동기화)·SP2(dock)·SP3(소스 토글) 완료 → **SP4(공개 뷰어)**.
> 확정 아키텍처: 소유자는 홈 SPA(`/{name}`, URL 유지), `/{name}/meeting` 은 무인증 공개 자막 뷰어.

## 목표

`/{name}/meeting` 을 **인증 없이 외부에서 자막만 보는** 페이지로 만든다. 음성 입력 불가, 소유자의
사적 대화(채팅)·TTS 오디오·제어 이벤트는 노출 안 됨.

## 확정 결정

- public 뷰어 = **자막 계열 이벤트만** + **라이브만**(replay 버퍼 미전송, 프라이버시·단순).
- `/{name}/meeting` = **별도 `viewer.html`**(무인증, 입력/로그인/채팅 없음).
- 소유자 SPA(`app.html`)는 회의 진입 시 **URL 을 /meeting 으로 바꾸지 않음**(in-place 뷰 전환).

## 컴포넌트 변경

### relay 라우트 (`jarvis-web/src/index.ts`)

- **신규** `GET /watch/:key` — **무인증**(토큰 검사 없음). WS 업그레이드면 `forwardToDO(..., "watch")`.
- `/{name}/meeting` 핸들러: `APP_HTML` → **`VIEWER_HTML`** 반환(신규 import). `/{name}` 은 `APP_HTML` 유지.
- `forwardToDO` role 유니온에 `"watch"` 추가.

### DO 역할 필터 (`jarvis-web/src/meeting_do.ts`)

- viewers 를 `Set<WebSocket>` → **`Map<WebSocket, "owner"|"public">`**.
- `attachViewer(ws, role)` — role 인자 추가. `fetch` 에서 `subscribe`→"owner", `watch`→"public".
- **public 허용 kind**(자막): `hello`, `source`, `translation_ko`, `translation_en`, `partial`,
  `gap`, `info`, `end`, `kicked`, `publisher_disconnected`. 그 외(`user`,`assistant`,`navigate`,
  `mic_source`)는 public 에 미전송.
- `broadcast(ev)`: owner 전체 / public 은 허용 kind 만. `broadcastBinary(data)`(TTS): **owner 만**.
- **replay**(attachViewer 접속 시): **owner 만** 기존 이벤트·`lastMicSource` 재생. public 은 재생 안 함(라이브만).
- `watch` 역할 등록 + close/error 정리(Map 에서 제거).

### 공개 뷰어 페이지 (`jarvis-web/src/static/viewer.html` 신규)

- 최소 페이지: 헤더(제목 + 연결 상태) + 자막 로그(`#log` 카드) + 스크롤락 lockbar. **로그인·dock·
  채팅·음성·메뉴 없음.**
- `key` = 경로 첫 세그먼트. `/watch/{key}` 무토큰 WS 구독(`wss`/`ws`).
- 자막 렌더: app.html 의 회의 자막 로직 이식(축소) — `hello`→제목, `source`/`translation_*`/`partial`
  /`gap`/`info`/`end`/`kicked`/`publisher_disconnected` 처리, `newCard`/`escapeHtml`/스크롤락.
  바이너리·채팅·navigate 는 안 옴(서버 필터). 재연결 백오프.
- `end`/`publisher_disconnected` 시 상태 표시. 회의 없으면 빈 로그(라이브 대기).

### SPA navigate 분리 (`jarvis-web/src/static/app.html`)

- `showView(v)`: `setView(v)` 만, **`history.pushState` 제거**(URL 안 바꿈 — 소유자는 /{name} 유지).
- `pathIsMeeting()`/`popstate` 리스너: app.html 은 이제 `/{name}` 에서만 로드되므로 /meeting 초기
  판정 불필요 — 초기 `setView("home")` 고정, popstate 리스너 제거. (`navigate` 이벤트가 뷰 전환 담당.)

### 빌드

- `viewer.html` 도 기존 `[[rules]] type="Text"` 로 문자열 번들(추가 설정 불필요). `index.ts` 에서
  `import VIEWER_HTML from "./static/viewer.html"`.

## 데이터 흐름

```
외부인 → GET /{name}/meeting → VIEWER_HTML
  → ws /watch/{key} (무인증) → DO.attachViewer(ws, "public")
  → 회의 자막 이벤트만 broadcast(public 필터) → 카드 렌더
소유자 → /{name} (APP_HTML, ADMIN /subscribe = owner) → 전체 스트림(채팅·TTS·navigate·자막)
  회의 navigate(meeting) → showView (URL 그대로 /{name})
```

## 엣지 케이스

- **공개 뷰어에 stale 자막**: replay 미전송이라 과거 회의 자막 안 보임(라이브만). 중간 합류 시 합류
  이후 자막만.
- **회의 미진행 중 /meeting 열기**: 빈 로그 + 라이브 대기(이벤트 오면 표시).
- **키 추측**: 공개 뷰어는 URL(=USER_NAME 키)만 알면 열람 — 의도된 공개. 입력은 불가(읽기 전용).
- **navigate 누수 방지**: public 필터가 navigate 제외 → 공개 뷰어가 홈으로 튕기지 않음.
- **소유자가 /meeting 직접 열기**: 이제 공개 뷰어(읽기 전용)를 봄 — 소유자 제어는 /{name} 에서.
- **기존 /subscribe owner 동작**: 전체 스트림·replay 유지(회귀 없음).

## 테스트 전략

- **통합(`jarvis-web/scripts/mic_relay_check.mjs`):** publisher 가 `source`(자막) + `assistant`(채팅)
  발행 → `/watch`(무인증) viewer 는 `source` 수신·`assistant` 미수신 / `/subscribe`(admin) viewer 는
  둘 다 수신. `/watch` 가 토큰 없이 연결되는지.
- **라우트:** `/{name}/meeting` 이 viewer.html(자막 페이지 마커) 반환, `/{name}` 은 app.html 반환.
- **웹:** `viewer.html`·`app.html` 인라인 JS `node --check`.
- **수동 E2E:** 외부 브라우저(시크릿)로 `/{name}/meeting` → 로그인 없이 자막만 → 내 채팅/음성 안 보임.
  소유자 홈에서 `/meet`/메뉴로 회의 → 자막이 공개 뷰어에 흐름. 회의 종료 → 공개 뷰어 "회의 종료".

## 비범위

- 공개 뷰어 인증/접근 제한(의도적 공개). 과거 회의 다시보기(라이브만).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/index.ts` | `/watch/:key`(무인증) + `/{name}/meeting`→VIEWER_HTML + forwardToDO role |
| `jarvis-web/src/meeting_do.ts` | viewers Map<ws,role>, attachViewer(role), broadcast/Binary 필터, replay owner 한정 |
| `jarvis-web/src/static/viewer.html` (신규) | 무인증 공개 자막 뷰어 |
| `jarvis-web/src/static/app.html` | showView pushState 제거 + popstate/pathIsMeeting 단순화 |
| `jarvis-web/scripts/mic_relay_check.mjs` | public 필터 검증(watch=자막만, subscribe=전체) |
