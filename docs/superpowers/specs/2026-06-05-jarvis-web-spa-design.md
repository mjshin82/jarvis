# jarvis-web 단일 셸 SPA 설계

날짜: 2026-06-05

> 상위 비전: jarvis 범용 웹 컨트롤러. A(골격)·B(채팅 홈)·C(음성 모드 전환) 완료.
> 이 문서는 **C 의 후속 버그픽스/구조 개선** — 음성 모드 전환 시 `/meeting` 으로
> **페이지 리다이렉트**하면 마이크 연결이 끊기는 문제를 SPA 화로 해결한다.

## 문제

`location.href = "/{name}/meeting"` 는 **전체 페이지 리로드**다. 홈의 mic-take
(`home.html` 의 `getUserMedia` 스트림 + `/mic` WebSocket + AudioContext + ScriptProcessor)가
통째로 파기되고, `meeting.html` 에는 마이크 캡처 코드가 아예 없어(수신 전용) 폰으로 회의에
들어가는 순간 폰 마이크가 끊긴다. jarvis 쪽 `RemoteMicReceiver` 는 `/mic` WS 가 끊기면 idle
후 로컬(시스템) 마이크로 되돌아가, 회의 입력이 폰→시스템으로 바뀐다.

브라우저에서 `MediaStream`/`WebSocket` 은 페이지 이동을 넘어 살아남을 수 없다. 따라서
**페이지를 이동하지 않는다** — 홈/회의를 한 셸 안의 두 뷰로 만들고 in-place 로 전환한다.

## 목표

음성 모드 전환(navigate 이벤트) 시 **리로드 없이** 홈↔회의 화면을 전환해, mic-take 와
`/subscribe` WebSocket 이 끊김 없이 유지된다. 재연결·모바일 재탭 0.

## 아키텍처 — 한 셸, 두 뷰

새 파일 `src/static/app.html` 하나를 워커가 `/:name` 과 `/:name/meeting` **양쪽 모두 동일하게**
서빙한다. 기존 `home.html`/`meeting.html` 은 삭제한다.

셸 구성:
- **공용 상단바**(양 뷰에서 항상 보임): 로그인 오버레이, `#mic-src` 배지, `#conn` 상태,
  **`#mic-toggle` 버튼**(전역 — 어느 뷰에서도 마이크 토글), `#mic-bar` 레벨 미터.
- **`#home-view`**: 채팅 버블 컨테이너(`#chat`) — `addText(role, text)` + 바이너리 TTS 재생.
- **`#meeting-view`**: 자막 로그(`#log`) + 스크롤락 `#lockbar` + `#meta-badge`/`#title` — `handle(ev)`.
- CSS: `body[data-view="home"]` 면 home-view 표시·meeting-view 숨김, `="meeting"` 면 반대.

**마이크 캡처 서브시스템은 셸 레벨**(IIFE 최상위)에 둔다. `micStart`/`micStop`/`/mic` WS/
ScriptProcessor/wakeLock 이 뷰와 무관하게 살아 있어, 뷰 전환에도 끊기지 않는다. 이것이 B 의 핵심.

## 데이터 흐름

```
로드: 경로 끝이 "/meeting" 이면 data-view="meeting", 아니면 "home".
      getPw() 있으면 connect() (subscribe WS 1개).
subscribe WS 메시지:
  binary(ArrayBuffer) → playAudio(buf)                       # TTS 재생(전역)
  JSON → dispatch(ev):
    user / assistant            → addText (home-view #chat)
    source / translation_ko/en / partial / gap / end / kicked
       / info / publisher_disconnected → handle(ev) (meeting-view #log)
    hello                       → applyMeta (#title, #meta-badge)
    mic_source                  → #mic-src 배지(공용)
    navigate                    → showView(ev.text)          # "meeting" | "home"

showView(view):
  body.dataset.view = view
  history.pushState({}, "", view === "meeting" ? `/${name}/meeting` : `/${name}`)
  # subscribe WS · mic-take 그대로 유지 (재연결 없음)

popstate(뒤로가기): 경로 재해석 → showView (리로드 없이 뷰 전환)
mic-toggle 클릭: micStart()/micStop() — 어느 뷰에서나 동작, 전환에도 유지
```

navigate 이벤트는 **현재 viewer 에게만 broadcast**(이미 직전 커밋에서 replay 버퍼 제외 처리됨)
이므로, 새로고침 후 stale navigate 로 잘못 전환되는 문제는 없다.

## 통합 정리 (중복 제거)

- 경로 파생 변수 `name`/`key` → **`name` 하나**로 통일.
- 로그인 show/hide → `.hidden` 클래스 하나로 통일(meeting 의 `style.display` 방식 폐기).
- `connect()`·재연결 백오프(500ms→8s)·인증 실패(close 1006/1008 → localStorage 제거 + showLogin)
  → 1벌로 통합. 바이너리 분기 포함.
- `escapeHtml` 은 자막(innerHTML) 렌더에 유지. 채팅 버블은 `textContent` 라 불필요.
- `#conn` 클래스 토글(`ok`/`bad`)은 meeting 쪽 방식을 채택(더 풍부).

## 워커 / 빌드 변경

**`src/index.ts`:**
- import: `MEETING_HTML`/`HOME_HTML` 제거 → `import APP_HTML from "./static/app.html"`.
- `/:name/meeting` 핸들러: `APP_HTML` 반환(헤더 동일: `text/html; charset=utf-8`, `no-store`).
- `/:name` 핸들러: `APP_HTML` 반환.
- 그 외 라우트(healthz/subscribe/publish/mic/mic-recv/notFound)·인증 헬퍼 변경 없음.

**빌드:** 기존 `wrangler.toml` 의 `[[rules]] type="Text" globs=["**/*.html"]` 가 `app.html` 을
문자열로 그대로 번들. `src/static.d.ts` 의 `*.html` 선언도 그대로 적용. 추가 설정 없음.

## 엣지 케이스

- **북마크로 `/{name}/meeting` 직접 진입**: meeting 뷰부터 표시. 마이크는 모바일 제스처 정책상
  탭이 필요(현행과 동일) — 연속성 보장은 *앱 내 navigate 경로* 한정, 직접 진입은 비범위.
- **뒤로가기/앞으로가기**: `popstate` 에서 경로 재해석해 `showView` — 리로드 없음, 마이크 유지.
- **이미 해당 뷰로 navigate**: `showView` 가 같은 뷰면 무해(중복 pushState 만 방지하면 됨).
- **로그인 전 navigate**: subscribe 가 아직 없으면 navigate 도 안 옴 → 무관.
- **회의 자막이 채팅 뷰에 있을 때**: 두 뷰 DOM 이 항상 존재하므로, 숨은 뷰도 백그라운드로 갱신됨
  (전환 시 이미 채워져 있음). 허용.

## 테스트 전략

- **자동(jarvis-web):** `mic_relay_check.mjs` 또는 신규 소형 체크 — 워커가 `/{name}` 과
  `/{name}/meeting` 두 경로에 **동일한 셸 HTML**(200 + `text/html`)을 반환하는지 확인.
  (HTTP 라우팅이라 기존 WS 체크와 별도 fetch 로 검증.)
- **typecheck:** `cd jarvis-web && npm run typecheck` (index.ts import 변경).
- **수동 E2E:** 폰 → 홈 → mic-take → "미팅 모드로 변경해줘" → **리로드 없이** 회의 뷰 전환 +
  URL `/Concode/meeting` + 마이크 유지(jarvis 가 계속 remote 소스, 끊김 없음) →
  "회의 끝내줘" → 홈 뷰 복귀 + 마이크 유지. 뒤로가기로도 뷰 전환 확인.

## 비범위 / 연기

- `/mic` 자체 인증·last-wins 등 기존 동작 변경 없음.
- 북마크 직접 진입 시 마이크 자동 재개(모바일 제스처 우회)는 비범위.
- 자막/채팅 렌더 로직 자체의 개선은 없음 — 기존 동작을 그대로 옮긴다.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `jarvis-web/src/static/app.html` | 신규 — home+meeting 통합 셸(두 뷰 + 공용 마이크/로그인/subscribe + showView/pushState) |
| `jarvis-web/src/static/home.html` | 삭제 |
| `jarvis-web/src/static/meeting.html` | 삭제 |
| `jarvis-web/src/index.ts` | HTML import 통합 → 두 라우트가 `APP_HTML` 서빙 |
| `jarvis-web/scripts/mic_relay_check.mjs` (또는 신규) | 두 경로 동일 셸 반환 검증 |
