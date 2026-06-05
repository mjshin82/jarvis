# jarvis-web 골격 (서브프로젝트 A) 설계

날짜: 2026-06-05

> 상위 비전: `meeting-web` 을 jarvis 의 범용 웹 컨트롤러 `jarvis-web` 으로 키운다.
> 분해: **A. 골격(리네임+라우팅+로그인)** → B. 채팅 홈(음성 대화) → C. 음성 모드전환.
> 이 문서는 **A** 만 다룬다.

## 목표

`meeting-web` 을 `jarvis-web` 으로 리네임하고, URL 을 `{host}/{name}`(홈) /
`{host}/{name}/meeting`(회의)로 재구성한다. 전체 프론트를 `ADMIN_PASSWORD` 로그인으로
게이트한다. 마이크 take 컨트롤을 회의 페이지에서 홈으로 옮긴다. 회의 자막 기능은 새
URL 에서 그대로 동작한다. (채팅 음성 대화·음성 모드전환은 B/C 로 연기.)

`{name}` = `.env` 의 `USER_NAME`(= `config.ROOM_KEY`, 예 `Concode`). jarvis 는 이 key 로
relay 에 붙는다.

## 리네임 & 배포

- 디렉터리 `meeting-web/` → `jarvis-web/` (git mv).
- `wrangler.toml`: `name = "meeting-web-jarvis"` → `name = "jarvis-web"`.
- 새 배포 URL: `https://jarvis-web.mjshin82.workers.dev`.
- 배포 절차: `jarvis-web` 배포 → `wrangler secret put RELAY_TOKEN` / `ADMIN_PASSWORD`
  (새 worker 라 재설정) → jarvis `.env` 의 `RELAY_URL` 을 새 URL 로 갱신 → 옛
  `meeting-web-jarvis` worker 삭제.
- Durable Object 클래스명(`MeetingDO`)·바인딩·migration 태그는 **유지**(리네임 시
  migration 충돌 위험 — 내부 구현이라 그대로 둔다).

## 라우팅

**HTML 페이지 (신규 경로, 로그인 게이트):**
- `GET /:name` → 홈 HTML (`home.html`).
- `GET /:name/meeting` → 회의 자막 뷰 HTML (`meeting.html`).

**WS 엔드포인트 (평면 유지 — jarvis 측 경로 무변경):**
- `/subscribe/:key`, `/publish/:key`, `/mic/:key`, `/mic-recv/:key`.
- jarvis 의 `relay_client`(→`/publish/<ROOM_KEY>`)·`RemoteMicReceiver`(→`/mic-recv/
  <ROOM_KEY>`)·웹의 `/mic` 송신은 경로 스킴 동일, RELAY_URL 만 변경.

**라우트 충돌:** `/healthz` 와 2-세그먼트 리터럴 prefix(`/subscribe/:key` 등)가
`/:name`·`/:name/meeting` 보다 먼저/우선 매칭되도록 Hono 에 **구체 라우트를 먼저 등록**.
(`/:name/meeting` 은 둘째 세그먼트가 리터럴 `meeting` 일 때만, `/subscribe/:key` 등은
첫 세그먼트가 리터럴이라 구분됨.)

## 로그인 (전체 게이트)

- 단일 자격증명 `ADMIN_PASSWORD`. 페이지 진입 시 `localStorage["jarvis_admin_pw"]` 없으면
  **비번 입력 오버레이** 표시 → 저장 후 본문 노출. (두 페이지에 인라인 공통 스니펫)
- 실질 게이트는 **데이터 WS 토큰**:
  - `/subscribe/:key` 를 **`requireAdmin`(ADMIN_PASSWORD)** 로 변경(기존 공개 → 게이트).
    페이지가 저장 토큰을 `?token=` 으로 붙여 연결.
  - `/mic`(ADMIN_PASSWORD)·`/mic-recv`(RELAY_TOKEN)·`/publish`(RELAY_TOKEN) 는 그대로.
- WS 가 401(close 1006/1008)이면 저장 토큰 폐기 + 로그인 오버레이 재노출.

## 페이지 구성

**홈 `/:name` (A=골격):**
- 로그인 오버레이 → 본문.
- 🎙️ **mic-take 토글 + 소스 배지** — `meeting.html` 의 캡처 로직 이식(getUserMedia →
  16kHz Int16 → `/mic/<name>?token=` 송신, Wake Lock, 정직한 게이지, kicked/no_receiver
  처리).
- 🟢 **"회의 모드로" 링크/버튼** → `/{name}/meeting`.
- 💬 채팅 영역 **플레이스홀더**("음성 대화는 다음 단계" — B 에서 구현).
- `/subscribe/<name>?token=` 연결 → `mic_source` 이벤트로 소스 배지 갱신.

**회의 `/:name/meeting` (뷰 전용):**
- 기존 자막 렌더링 유지 + 로그인 오버레이.
- **admin-bar(mic 토글·admin 잠금 해제)는 제거** — mic-take 는 홈으로 이동. 소스 배지만
  표시(display).
- `/subscribe/<name>?token=` 로 연결(게이트). 페이지 key 는 경로 `/:name/meeting` 에서 추출.

## 컴포넌트 변경

| 파일 | 변경 |
|------|------|
| `jarvis-web/` | `meeting-web/` 에서 `git mv` |
| `jarvis-web/wrangler.toml` | `name = "jarvis-web"` |
| `jarvis-web/src/index.ts` | `/:name`·`/:name/meeting` 라우트(+`home.html` import), `/subscribe` → `requireAdmin`, forwardToDO 그대로, 라우트 주석 갱신 |
| `jarvis-web/src/static/home.html` | 신규 — 로그인 + mic-take(+배지) + nav + 채팅 placeholder |
| `jarvis-web/src/static/meeting.html` | admin-bar 제거(배지만), 로그인 오버레이, `/subscribe?token=`, key 를 `/:name/meeting` 에서 추출 |
| `jarvis-web/scripts/mic_relay_check.mjs` | `/subscribe` 인증 추가 검증, 경로 갱신 |
| jarvis `main.py` | 시작 URL 박스 `/{ROOM_KEY}` (홈) — 새 worker URL 은 `.env`에서 |
| jarvis `.env` | `RELAY_URL` = `wss://jarvis-web.mjshin82.workers.dev` (배포 후 제가 갱신) |

## 에러 / 엣지

- **비번 오류:** 데이터 WS 401 → localStorage 비움 + 로그인 오버레이.
- **회의 자막 뷰 공유:** 전체 게이트라 참석자도 `ADMIN_PASSWORD` 필요(설계 결정 A).
- **mic-take 는 홈에서만:** 회의 페이지엔 토글 없음(소스 배지만). 동적 회의가 현재 소스를
  따르므로, 홈에서 mic 켜둔 채 회의로 이동하면 회의가 그 소스를 씀.
- **라우트 우선순위:** 구체 라우트 먼저 등록 안 하면 `/:name` 이 `/healthz` 등을 삼킴 →
  등록 순서로 방지.

## 테스트 전략

- **typecheck:** `cd jarvis-web && npm run typecheck`.
- **통합(`mic_relay_check.mjs`):** 기존(무토큰 거부·binary 포워딩·mic_source) + 신규
  `/subscribe` 무토큰 거부 / 토큰 통과. `wrangler dev` 대상.
- **수동 E2E:** `/{name}` 접속 → 로그인 → mic-take(게이지) → "회의 모드로" → `/{name}/
  meeting` 자막 뷰. 잘못된 비번 → 거부.

## 연기 (B/C)

- 채팅 음성 대화(jarvis 메인 대화의 웹 발행 + 채팅 UI) — B.
- 음성 모드전환("미팅모드로 변경해줘" → /meet + 웹 navigate) — C.
- DO 클래스명 리네임(`MeetingDO`→`JarvisDO`) — migration 위험으로 보류.
