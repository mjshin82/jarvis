# viewer→목록 네비게이션 + 목록 i18n + 진행중 회의 on-air 설계

> 상태: 승인됨 (2026-06-07). **전부 jarvis-web 전용 — 파이썬/meetings.db 변경 없음.**

## 목표

1. viewer(`/{room}/meeting/{mid}`) 상단 좌측 아이콘을 누르면, **admin 권한이 있을 때** 회의 목록
   페이지(`/{room}/meeting`)로 이동.
2. 목록 페이지(`list.html`)에 방금 구축한 공용 i18n 시스템 적용(en/ko/ja, `?lang=` 우선).
3. 진행 중인 회의가 있으면 목록 **최상단**에 on-air 행으로 띄우고 🔴 ON AIR 표시.
   목록 페이지가 열려 있는 동안 회의 시작/종료가 **실시간**으로 반영(새로고침 불필요).

세 변경은 서로 독립적이지만 한 plan/브랜치에서 진행한다(모두 jarvis-web, 같은 파일군 i18n.js·list.html 공유).

## 배경 (현 구조)

- DO(`meeting_do.ts`)는 방(room) 1개당 1 인스턴스(`MEETING_DO.idFromName(roomKey)`). 라이브 회의는
  그 방의 DO를 통해 흐른다. DO는 **인스턴스 메모리**에 라이브 상태를 이미 보유:
  - `publisher: WebSocket | null` — jarvis 연결 여부(라이브 신호).
  - `currentMeetingId`, `currentPasswordHash` — `meeting_creds`(jarvis→DO, 미broadcast)에서 설정,
    `end`/`navigate`(홈) 시 클리어.
  - `lastMeetingTitle` — `meeting_title`에서, `meta`(MeetingMeta) — `hello`에서.
- 목록 데이터: list.html → `/watch/{room}`(admin) → `{kind:"list"}` → DO가 `list_request`를 publisher
  (jarvis)로 포워딩 → jarvis가 `meetings.db`의 **저장(종료)된** 회의만 `list_response` → DO →
  `meeting_list`. 진행 중(미저장) 회의는 이 목록에 **없다**.
- **중요:** list.html의 admin watch 소켓은 `attachWatchPending` 클로저에 머물며 `list`/`delete`만
  반복 처리한다. `attachViewer`를 호출하지 않으므로 **`this.viewers`에 등록되지 않는다.** 따라서
  on-air 푸시를 위해 admin watcher를 추적하는 **별도 셋**이 필요하다.
- admin 판정: `/watch`에 `?token=ADMIN_PASSWORD`. Worker가 검증 후 DO에 `admin=1` 내부 플래그 부여
  (클라 위조분 제거). DO는 `attachWatchPending(ws, isAdmin)`로 수신.
- 클라 측 admin 신호: `localStorage["jarvis_admin_pw"]` 존재 여부.

## 변경 1 — viewer 아이콘 → 목록 (admin 한정)

`viewer.html`의 `<img class="app-icon">`(L174 부근)을 admin일 때만 클릭 가능하게 한다.

- 본문 IIFE 시작부에 이미 `adminPw = localStorage.getItem("jarvis_admin_pw")`, `key = _parts[0]` 존재.
- 로드 시 `if (adminPw)`이면 아이콘에 `style.cursor = "pointer"`, `title` 부여, 클릭 리스너 추가:
  `location.href = "/" + encodeURIComponent(key) + "/meeting"`.
- 비-admin이면 아무 것도 안 함(장식용 유지). 새 의존성/이벤트 없음.
- 접근성: 클릭 가능할 때 `alt`/`title`을 i18n(`nav.toList`)로. (장식 아이콘이라 `alt=""`였음 — admin일
  때만 의미 부여.)

## 변경 2 — list.html i18n

공용 `i18n.js`를 로드하고 정적/동적 문자열을 치환. 카탈로그에 `list.*` 키 추가(ko/en/ja).

### 정적 (data-i18n / data-i18n-ph)

| 위치 | 현재(ko) | 키 |
|------|---------|-----|
| `<title>` (L6) | 회의 목록 | `list.title` |
| gate 제목 (L30) | 🔒 관리자 로그인 | `list.adminTitle` |
| gate placeholder (L31) | 관리자 비번 | `list.adminPwPlaceholder` |
| gate 버튼 (L32) | 입장 | `gate.enter` (기존 재사용) |
| header (L35) | 최근 회의 | `list.header` |

### 동적 (I18N.t(...))

| 위치 | 현재(ko) | 키 |
|------|---------|-----|
| 로딩 (L36,96) | 불러오는 중… | `list.loading` |
| 빈 상태 (L47) | 저장된 회의 없음 | `list.empty` |
| 회의 기본 제목 (L56) | 회의 | `list.defaultTitle` |
| 삭제 버튼 title 속성 (L60) | 삭제 | `list.deleteTitle` |
| 삭제 confirm (L62) | 이 회의를 삭제할까요? | `list.deleteConfirm` |
| 관리자 전용 close 메시지 (L86) | 관리자 전용입니다. | `list.adminOnly` |

- 로드 순서: viewer와 동일하게 `<head>`에 블로킹 `<script src="/i18n.js">`를 본문 inline script 앞에.
- 초기 로딩 메시지 "불러오는 중…"은 HTML에 `data-i18n="list.loading"`로 두되, 런타임에서 다시 세팅하는
  L96은 `I18N.t("list.loading")` 사용(둘 다 같은 키).

### 신규 카탈로그 키 값 (ko / en / ja)

```
list.title:               회의 목록 / Meetings / 会議一覧
list.adminTitle:          🔒 관리자 로그인 / 🔒 Admin login / 🔒 管理者ログイン
list.adminPwPlaceholder:  관리자 비번 / Admin password / 管理者パスワード
list.header:              최근 회의 / Recent meetings / 最近の会議
list.loading:             불러오는 중… / Loading… / 読み込み中…
list.empty:               저장된 회의 없음 / No saved meetings / 保存された会議はありません
list.defaultTitle:        회의 / Meeting / 会議
list.deleteTitle:         삭제 / Delete / 削除
list.deleteConfirm:       이 회의를 삭제할까요? / Delete this meeting? / この会議を削除しますか？
list.adminOnly:           관리자 전용입니다. / Admins only. / 管理者専用です。
list.onAir:               🔴 ON AIR / 🔴 ON AIR / 🔴 ON AIR        (전 로케일 동일 — 보편 관용구)
list.liveDefault:         진행 중인 회의 / Live meeting / 進行中の会議
nav.toList:               회의 목록 / Meeting list / 会議一覧        (viewer 아이콘 title/alt)
```

`…`는 U+2026, 일본어 물음표는 전각 `？`. 카탈로그 값은 i18n 테스트에서 정확 일치 검증.

## 변경 3 — 진행중 회의 on-air (실시간)

### 신규 이벤트

`types.ts`의 `EventKind`에 `meeting_live` 추가. DO→admin watcher 전용.
text = JSON. 두 형태:
- 라이브: `{ "live": true, "id": "<mid>", "title": "<string|null>" }`
- 비라이브: `{ "live": false }`

`PUBLIC_KINDS`에는 **넣지 않는다** — `broadcast()`(viewers 대상)가 아니라 adminWatchers에 직접
`safeSend`하므로 public 필터를 거치지 않는다.

### DO 상태/로직 추가

- 신규 필드: `private adminWatchers: Set<WebSocket> = new Set();`
- `attachWatchPending`에서 admin이 `{kind:"list"}` 또는 `{kind:"delete"}`를 보낼 때(=목록 페이지로
  확정), `this.adminWatchers.add(ws)` 하고 즉시 `this.sendLiveStatus(ws)`로 현재 스냅샷 1회 전송.
  (기존 `list_request`/`delete_request` 포워딩은 그대로.)
  - 현 코드상 `list`/`delete`는 `if (!this.publisher) close(4003 "no-meeting")` 가드가 먼저다. 즉
    publisher가 없으면 목록 자체가 안 열린다(기존 동작 유지). publisher가 있을 때만 등록·스냅샷.
- `ws` close 핸들러(현재 `clearTimeout(timer)`만)에서 `this.adminWatchers.delete(ws)` 추가.
- 라이브 판정 헬퍼: `private isLive(): boolean { return this.publisher != null && this.currentMeetingId != null; }`
- 제목 헬퍼: `private liveTitle(): string | null { return this.lastMeetingTitle ?? (this.meta ? this.meta.partner + " ↔ " + this.meta.user : null); }`
- `private sendLiveStatus(ws)`: `isLive()`면 `{live:true,id:currentMeetingId,title:liveTitle()}`,
  아니면 `{live:false}`를 `safeSend(ws, buildEvent({kind:"meeting_live", text: JSON.stringify(...)}))`.
- `private broadcastLiveStatus()`: `for (const ws of this.adminWatchers) this.sendLiveStatus(ws);`

### broadcastLiveStatus 호출 시점

- `meeting_creds` 처리 후(currentMeetingId 설정 직후) → 라이브 시작 → `broadcastLiveStatus()`.
- `meeting_title` 처리 시 → 제목 갱신 반영 → (라이브면) `broadcastLiveStatus()`.
- `end` 처리(상태 클리어 후) → `broadcastLiveStatus()` (live:false).
- `navigate`로 홈 복귀(`msg.text !== "meeting"`, 상태 클리어 후) → `broadcastLiveStatus()` (live:false).
- publisher `onClose`(기존 `publisher_disconnected` broadcast와 함께) → `broadcastLiveStatus()`.
  `attachSlot` close는 `set(null)` 후 `onClose()` 실행 → `publisher`가 이미 null → `isLive()=false` →
  live:false 전송.

### list.html 렌더링

- `#list` 위에 on-air 컨테이너 `#onair` 추가(빈 상태로 시작).
- 메시지 핸들러에 분기 추가: `ev.kind === "meeting_live"` →
  - `JSON.parse(ev.text)`의 `live`가 true이고 `id` 있으면: on-air 행을 렌더/갱신.
    - 내용: 좌측 `🔴 ON AIR` 배지(`list.onAir`) + 제목(`title` 또는 `I18N.t("list.liveDefault")`).
    - 클릭 시 `location.href = /{room}/meeting/{id}` (admin은 비번 없이 라이브 뷰어 입장).
    - 시각적 강조: 빨강 테두리/배지(아래 스타일).
  - `live`가 false이면: `#onair` 비움.
- 저장 목록(`meeting_list`) 렌더는 기존 그대로. 라이브 회의는 아직 미저장이라 저장 목록과 **중복 없음**.
  회의 종료 후에는 다음 페이지 로드 시 일반 목록에 나타난다(현재는 자동 새로고침 안 함 — YAGNI).

### on-air 스타일(개략)

`.onair-row` = `.row` 기반 + `border-color: #e11d48`(빨강), 좌측 `.onair-badge`(빨강 글자, 작은 폰트,
점멸은 과하므로 정적). 다크모드는 기존 변수 팔레트로 자연스럽게 처리(고정 빨강 유지).

## 데이터 흐름 (on-air, 실시간)

1. admin이 list.html 진입 → `/watch/{room}?token` 연결 → `{kind:"list"}` 송신.
2. DO: (publisher 있음) → `adminWatchers.add(ws)` + `sendLiveStatus(ws)`(초기 스냅샷) + 기존 list_request.
3. list.html: `meeting_list` → 저장 목록 렌더; `meeting_live` → on-air 행 렌더(있으면).
4. 회의 시작(같은 방): jarvis publish → `hello`/`meeting_creds` → DO `broadcastLiveStatus()` →
   admin watcher가 live:true 수신 → on-air 행 등장. `meeting_title` 도착 시 제목 갱신.
5. 회의 종료: `end`/publisher 끊김 → DO `broadcastLiveStatus()`(live:false) → on-air 행 제거.

## 에러 처리

- `meeting_live` 파싱 실패/`id` 없음 → list.html은 무시(행 미생성).
- on-air 행 클릭 후 회의가 막 종료된 경우 → 라이브 뷰어가 archive/일반 흐름으로 폴백(기존 동작). 허용.
- viewer.html(공개/owner viewer)은 `meeting_live`를 받지 않는다(adminWatchers 한정). 설령 받아도
  `handle()`의 `default: return`으로 무시.
- 아이콘 클릭은 admin이 아니면 리스너 자체가 없음 → 무동작.

## 테스트 전략 (정직하게)

- **i18n.js 카탈로그 신규 키**(`list.*`, `nav.toList`): 기존 vitest 패턴으로 `_t` 단위 테스트 추가
  (ko/en/ja 대표 키 정확 일치). 저렴하고 확실.
- **DO `meeting_live` 로직 / list.html 렌더 / viewer 아이콘**: jarvis-web에 워커 런타임 테스트
  하네스가 없어 단위 테스트 불가. `npm run typecheck` + `npx wrangler deploy --dry-run` +
  **수동 검증**(회의 시작/종료하며 목록에서 on-air 등장·소멸, 아이콘 클릭 이동, `?lang=` 전환)으로 확인.
  워커 테스트 풀(@cloudflare/vitest-pool-workers) 도입은 이 토이 프로젝트엔 과함 → 비포함.

## 적용 범위

- 변경 파일: `viewer.html`(아이콘), `list.html`(i18n+on-air), `i18n.js`(카탈로그), `types.ts`
  (meeting_live), `meeting_do.ts`(adminWatchers+live 브로드캐스트).
- 파이썬/`meetings.db`/`index.ts` 변경 없음.

## 배포

[[auto-deploy-web]]: 머지 후 `cd jarvis-web && npx wrangler deploy` 자동 실행. jarvis 재시작/push는 사용자 몫.
