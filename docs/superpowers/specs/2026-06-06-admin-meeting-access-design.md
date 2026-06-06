# 중계 페이지 관리자 권한 (비번 생략 + 회의 목록) 설계

날짜: 2026-06-06

## 목표
1. **관리자 비번 생략**: 개별 회의 중계(`/{room}/meeting/{mid}`) 입장 시 관리자면 회의 비번 없이 열람.
2. **관리자 전용 목록**: `/{room}/meeting`(mid 없음)을 신규 관리자 전용 페이지로 — 저장된 최근 회의 최대 20개를 시간순(최근 먼저) 리스트로, 클릭하면 해당 회의 열람.

## 핵심 원칙: 관리자 검증은 Worker, DO 는 플래그 신뢰
관리자 = `ADMIN_PASSWORD`. Worker `requireAdmin()`(`?token=` 또는 Bearer)가 단일 검증 지점. `/watch` 연결에 유효 관리자 토큰이 붙으면 Worker 가 DO 내부 경로에 `admin=1` 을 실어 전달하고, DO 는 이 플래그를 신뢰(Worker↔DO 내부 통신). DO 에 ADMIN_PASSWORD 를 주지 않는다. 데이터(기록·목록)는 모두 jarvis SQLite 온디맨드(DO 무보관) — 기존 archive 패턴 답습.

## 비범위 (YAGNI)
- 목록 페이지네이션(20 고정)·검색·필터·삭제.
- 관리자 비번을 viewer/목록 페이지에서 변경하는 UI(읽기·프롬프트만).
- 관리자 라이브 viewer 의 owner 전용 이벤트 수신(공개 자막 kind 로 충분).

---

## 인증 흐름 (공통)
- 관리자 비번은 owner 앱과 **동일 localStorage 키 `jarvis_admin_pw`** 재사용(같은 origin). 같은 브라우저로 owner 앱 로그인 시 자동 관리자. 없으면 페이지가 비번 프롬프트(입력 시 같은 키에 저장).
- `/watch/:key?token=<adminpw>`: Worker `requireAdmin` 통과 → `forwardToDO(..., {admin:true})` → 내부 URL `/__do/watch/{key}?admin=1`. 토큰 없음/불일치 → 기존 공개(비번 게이트) 흐름.

---

## ① 개별 회의 관리자 비번 생략

### Worker (index.ts)
- `/watch/:key`: 핸들러에서 `const admin = requireAdmin(c);` → `forwardToDO(c.env, key, "watch", c.req.raw, admin)`.
- `forwardToDO(..., admin=false)`: admin 이면 내부 URL 에 `?admin=1` 추가.

### DO (meeting_do.ts)
- `fetch`: watch 라우팅에서 `const isAdmin = url.searchParams.get("admin") === "1";` → `attachWatchPending(server, isAdmin)`.
- `attachWatchPending(ws, isAdmin)`: 첫 메시지 처리에서
  - `{kind:"auth", mid, pw}`:
    - **isAdmin** → 회의 비번 검증 생략: 라이브(`mid===currentMeetingId`)면 `attachViewer(ws,"public")`; 아니면 `archive_request{req, mid, admin:true}` 전달(+pending).
    - 비관리자 → 기존 흐름(라이브 pw 검증 / 종료 archive_request{req,mid,pw}).

### jarvis (_serve_archive)
- `meeting_store.archive_response` 를 **`archive_response(row, pw, req, *, admin=False)`** 로 확장: `admin` 이 참이면 해시 비교를 건너뛰고(비번 무시) row 가 있으면 ok+기록, 없으면 ok:false.
- `_serve_archive` 는 `archive_response(store.get(mid), pw, req, admin=bool(data.get("admin")))` 로 호출(admin 플래그는 data text 에서). DO 가 admin 검증 후에만 `admin:true` 를 보내므로 jarvis 는 신뢰.

### viewer.html (`/{room}/meeting/{mid}`)
- 로드 시 `const adminPw = localStorage.getItem("jarvis_admin_pw") || "";`
- adminPw 있으면: 게이트 숨김, `/watch/{room}?token=<adminpw>` 로 연결 후 `{kind:"auth", mid, pw:""}` 전송(DO 가 admin 으로 비번 생략).
- adminPw 없으면: 기존 회의-비번 게이트.
- close 4003(관리자 토큰 무효/만료로 admin 미부여 → DO pw 게이트 실패) → 기존처럼 게이트 표시(폴백).

---

## ② 관리자 전용 최근 회의 목록 (`/{room}/meeting`)

### 신규 list.html + 라우트
- `index.ts`: `import LIST_HTML from "./static/list.html"`. `GET /:name/meeting` → `LIST_HTML`(현 VIEWER_HTML 대신). `GET /:name/meeting/:mid` → VIEWER_HTML 유지.
- list.html: 로드 시 adminPw(localStorage) 확인 — 없으면 비번 프롬프트(저장). `/watch/{room}?token=<adminpw>` 연결 → `{kind:"list"}` 전송. 비관리자/거부 → "관리자 전용" 안내.

### DO (meeting_do.ts)
- `attachWatchPending` 첫 메시지에 `{kind:"list"}` 처리:
  - `isAdmin` 아니면 `ws.close(4003,"admin-only")`.
  - isAdmin → `req=++archiveSeq; pendingArchive.set(req, ws);` → `safeSend(publisher, archive 와 동일 패턴으로 list_request{req})`; 타임아웃.
- `handlePublisherMessage` 에 `list_response{req, ok, meetings}` 처리: `ws=pendingArchive.get(req)` → `meeting_list{meetings}` 전송(미attach — 일회성). `meeting_list` 는 PUBLIC_KINDS 무관(특정 소켓 safeSend).

### jarvis
- `meeting_store.recent(limit=20)`: `SELECT id, title, started_at, ended_at FROM meetings ORDER BY started_at DESC LIMIT ?` → `[{id,title,started_at,ended_at}, ...]`.
- `relay_client._handle_inbound`: `list_request` → `self.on_list_request(m)` 콜백.
- `main`: `_serve_list(msg)` → `{req}` 파싱 → `meetings = store.recent(20)` → `web_pub.emit("list_response", json.dumps({"req":req,"ok":True,"meetings":meetings}))`. `web_pub.on_list_request = _serve_list`.

### list.html 렌더
- `meeting_list{meetings}` 수신 → 시간순(서버가 이미 desc) 카드/버튼 목록: 각 행 = 제목 + 시작시각(로컬 표시). 클릭 → `location.href = "/{room}/meeting/" + id`(관리자라 비번 없이 열람).
- 빈 목록 → "저장된 회의 없음".

### types.ts
- `EventKind` 추가: `"list"`(viewer→DO), `"list_request"`(DO→jarvis), `"list_response"`(jarvis→DO), `"meeting_list"`(DO→viewer). archive_request 의 text JSON 에 `admin` 필드(타입 무변경 — text 안).

---

## 데이터 흐름
관리자: localStorage 비번 → `/watch?token` → Worker requireAdmin → DO admin=1.
- 개별: auth(admin) → 라이브 즉시 / 종료 archive_request{admin} → jarvis 비번무시 회신 → meeting_archive.
- 목록: list(admin) → list_request → jarvis recent(20) → list_response → meeting_list → 클릭 → /{mid}.
비관리자: 기존 회의-비번 게이트(개별만; 목록 페이지는 "관리자 전용").

## 테스트
- jarvis: `tests/test_meeting_store.py` — `recent(limit)` 정렬·limit·필드. `archive_response(..., admin=True)` 가 비번 무시하고 ok+기록. `tests/test_relay_client.py` — `_handle_inbound` 의 `list_request` → `on_list_request` 호출.
- 웹/DO: `npm run typecheck` + list.html/viewer.html JS 구문. 수동: 관리자(owner 로그인 브라우저) → /{mid} 비번 없이 열람; /{room}/meeting 목록·클릭; 비관리자 → 개별은 비번 게이트, 목록은 차단.

## 검증
- `.venv/bin/python -m pytest -q` 통과.
- `cd jarvis-web && npm run typecheck` 0, list/viewer JS 구문 OK.
- 수동(배포·재시작): 관리자 비번 생략·목록 표시·클릭 열람; 비관리자 차단.

## 영향 파일
| 파일 | 변경 |
|---|---|
| `jarvis-web/src/index.ts` | `/watch` admin 토큰 → forwardToDO admin; `/:name/meeting` → LIST_HTML 라우트 |
| `jarvis-web/src/meeting_do.ts` | watch isAdmin, auth/list admin 분기, list_request/response, meeting_list |
| `jarvis-web/src/types.ts` | list·list_request·list_response·meeting_list kind |
| `jarvis-web/src/static/list.html` (신규) | 관리자 목록 페이지 |
| `jarvis-web/src/static/viewer.html` | adminPw 시 비번 생략 연결 |
| `meeting_store.py` | `recent(limit)` + `archive_response(..., admin=False)` |
| `relay_client.py` | `_handle_inbound` list_request + `on_list_request` |
| `main.py` | `_serve_list` 배선 + `_serve_archive` admin 분기 |
| `tests/test_*` | recent/archive admin/relay list 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
