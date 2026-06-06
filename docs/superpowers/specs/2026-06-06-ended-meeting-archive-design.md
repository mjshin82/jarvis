# 종료된 회의 열람 (기록 + 요약) 설계

날짜: 2026-06-06

## 목표
회의가 끝나도 중계방(`/{room}/meeting/{mid}`)에 비번으로 입장할 수 있고, 종료 상태에서는 그 회의의 **전체 대화 기록 + 언어별 요약**을 본다.

## 핵심 원칙: DO 는 순수 중계, jarvis 가 단일 소유자
컨트롤러(DO/Worker)는 여러 jarvis 가 붙는 공용 인프라다. 회의 데이터를 DO 에 두면 이중 보관 + 타 jarvis 노출 위험. 따라서 **DO 는 회의 데이터를 일절 보관하지 않고**, 열람 요청 시 jarvis 에 물어 그 viewer 에게만 중계한다. jarvis 가 켜져 있을 때만 열람 가능(의도).

## 비범위 (YAGNI)
- DO 영구/메모리 보관, 페이지네이션, 과거 회의 목록 UI.
- jarvis off 시 열람(불가 — 4003).
- owner 앱(app.html)의 종료 회의 열람(owner 는 종료 시 홈으로 가고 meetings.db 직접 보유 — 대상은 공개 중계 페이지 viewer.html).

---

## 데이터 흐름

### 신규 입장(종료된 회의)
1. viewer 가 `/watch/{room}` 연결 후 `{kind:"auth", mid, pw}` 전송.
2. DO: 라이브 회의 매칭(`mid==currentMeetingId && hash(pw)==currentPasswordHash`) → 기존 라이브 합류. 아니면 → archive 경로:
   - `this.publisher` 없으면(jarvis off) `ws.close(4003)`.
   - `req = ++archiveSeq`; `pendingArchive.set(req, ws)`; publisher 에 `archive_request {req, mid, pw}` 전송; 10초 타임아웃(미응답 → 4003).
3. jarvis `_handle_inbound` 의 `archive_request`: `store.get(mid)` →
   - 행 없거나 `hash_password(pw) != row["password_hash"]` → `emit("archive_response", {req, ok:false})`.
   - 일치 → `emit("archive_response", {req, ok:true, title, transcript, summaries})` (transcript=`json.loads(row["transcript"])`, summaries=`json.loads(row["summary"])` 또는 `{}`).
4. DO `archive_response {req, ok, ...}`: `ws = pendingArchive.get(req)`; 삭제. `ok` → 정제 `meeting_archive {title, transcript, summaries}`(비번/해시 없음) 를 ws 에 전송 + `attachViewer(ws, "public")`(이후 meeting_summary 수신용). `ok:false` → `ws.close(4003)`.
5. viewer: `meeting_archive` 렌더 — 로그 비었으면 트랜스크립트 채움 + 언어별 요약 패널.

### 요약 "준비되면" (전송, 보관 X)
- `_save_meeting` 백그라운드 요약 완료 후 → `emit("meeting_summary", {mid, summaries})`.
- DO `meeting_summary`: 현재 viewer 들에게 broadcast(공개). 라이브로 보던 사람·요약 전에 입장한 사람 모두 요약 패널 갱신. (입장 직후엔 archive_response 의 transcript 먼저, 요약은 이 푸시로.)

### 보안: eviction 을 "종료" → "새 회의 시작"으로 이동
- 현재: 종료(end/navigate-home) 시 공개 viewer 강제 해제(B1: 다음 회의 누수 방지).
- 변경: **종료/navigate-home 에서는 evict 안 함**(viewer 가 머물러 기록·요약 열람). 라이브 creds(currentMeetingId/hash/info/title)만 비움.
- **새 회의 시작(`meeting_creds`)**: creds 설정 전에 `evictPublicViewers()` — 이전 회의 보던 viewer 를 끊어 새 회의 비번 재인증 강제(B1 유지). 종료가 아니라 "새 회의 신원 등장"이 누수 경계.

---

## 컴포넌트

### relay_client.py
- `_handle_inbound`: `archive_request` kind 추가 처리 — `self.on_archive_request` 콜백(설정 시) 호출. (기존 `viewers` 유지.)
  ```python
  if m.get("kind") == "archive_request" and self.on_archive_request:
      self.on_archive_request(m)
  ```
- `__init__` 에 `self.on_archive_request = None`.
- `emit` 는 이미 임의 kind 지원 → `emit("archive_response", json.dumps({...}))`, `emit("meeting_summary", json.dumps({...}))`.

### main.py
- archive 조회 콜백 정의 후 `web_pub.on_archive_request = _serve_archive`:
  ```python
  def _serve_archive(msg):
      try:
          data = json.loads(msg.get("text") or "{}")   # payload 는 text(JSON)에 실려옴
      except Exception:
          return
      req = data.get("req")
      mid = (data.get("mid") or "")
      pw = (data.get("pw") or "")
      row = store.get(mid)
      ok = bool(row) and hash_password(pw) == (row.get("password_hash") or "")
      if not ok:
          web_pub.emit("archive_response", json.dumps({"req": req, "ok": False})); return
      try:
          transcript = json.loads(row.get("transcript") or "[]")
      except Exception:
          transcript = []
      try:
          summaries = json.loads(row.get("summary") or "{}") if row.get("summary") else {}
      except Exception:
          summaries = {}
      web_pub.emit("archive_response", json.dumps({
          "req": req, "ok": True, "title": row.get("title") or "회의",
          "transcript": transcript, "summaries": summaries}))
  ```
  (`store`, `hash_password`, `json` 모두 main 스코프에 있음.)
- `_save_meeting`: 요약 저장 직후 `web_pub.emit("meeting_summary", json.dumps({"mid": record["id"], "summaries": summaries}))`.

### meeting_do.ts
- 필드: `private pendingArchive: Map<number, WebSocket> = new Map();`, `private archiveSeq = 0;`. (회의 데이터 필드 없음.)
- `attachWatchPending` auth 분기: 라이브 매칭이면 기존. 아니면:
  - `if (!this.publisher) { ws.close(4003, "no-meeting"); return; }`
  - `const req = ++this.archiveSeq; this.pendingArchive.set(req, ws);`
  - `this.safeSend(this.publisher, this.buildEvent({ kind: "archive_request", text: JSON.stringify({ req, mid: msg.mid, pw: msg.pw }) }));`
  - `setTimeout(() => { if (this.pendingArchive.delete(req)) { try { ws.close(4003, "archive-timeout"); } catch {} } }, 10000);`
  - (라이브 viewer 맵에 즉시 넣지 않음 — archive_response 시 합류.)
- `handlePublisherMessage`: `archive_response` 처리(JSON 파싱) — `const { req, ok, title, transcript, summaries } = JSON.parse(msg.text||"{}")`; `ws = pendingArchive.get(req)`; `pendingArchive.delete(req)`; 없으면 return. `ok` → `safeSend(ws, buildEvent({kind:"meeting_archive", text: JSON.stringify({title, transcript, summaries})}))` + `attachViewer(ws, "public")`. else → `ws.close(4003)`. **append/broadcast 안 함**(특정 소켓 전용).
- `handlePublisherMessage`: `meeting_summary` → `broadcast(buildEvent(msg))`(공개 viewer 필터 통과). append 안 함.
- `meeting_creds` 케이스: creds 저장 **전에** `this.evictPublicViewers();` 추가(새 회의 → 이전 viewer 해제).
- `end`/`navigate`(home) 케이스: `evictPublicViewers()` 호출 **제거**(viewer 유지). creds/title/info 비우기는 유지.

### types.ts
- `EventKind` 추가: `"archive_request"`(DO→jarvis), `"archive_response"`(jarvis→DO), `"meeting_archive"`(DO→viewer), `"meeting_summary"`(jarvis→viewer).
- `PUBLIC_KINDS`(meeting_do.ts) 에 `"meeting_archive"`, `"meeting_summary"` 추가(viewer 공개). `archive_request`/`archive_response` 는 미포함(DO↔jarvis 전용).

### viewer.html
- `handle()` 에 추가:
  - `meeting_archive {title, transcript, summaries}`: 로그(`#log`)가 비어 있으면 transcript 각 항목을 source + translations 로 렌더(라이브로 본 사람은 이미 있으니 스킵). 항상 요약 패널(`#summary`, 신규 영역) 을 summaries 로 채움. 헤더 제목 title 반영. "회의 종료" 상태 표시.
  - `meeting_summary {summaries}`: 요약 패널 갱신(없으면 생성).
- 요약 패널 CSS/HTML: `#summary`(고정 또는 로그 위) — 언어별 블록(국기 + 요약 텍스트). 비어 있으면 숨김.
- `end` 케이스: 기존 "회의 종료" 표시 유지(요약은 meeting_summary/archive 로).

---

## 데이터 모델 참고 (meetings.db)
`meetings(id, password_hash, title, started_at, ended_at, transcript[JSON list], summary[JSON {lang:요약}])`. languages 컬럼 없음 — 요약은 summary 컬럼의 키로 언어 식별, 트랜스크립트 항목은 `{ts, source, src_lang, translations:{lang:text}}`.

## 테스트
- `tests/test_*`(jarvis): `_serve_archive` 동작 — 올바른 비번 → archive_response ok+transcript+summaries; 틀린 비번/없는 mid → ok:false. (store 는 tmp db, monkeypatch web_pub.emit 캡처.)
- relay_client: `_handle_inbound` 가 `archive_request` 시 `on_archive_request` 호출.
- 웹/DO: `npm run typecheck` + viewer/app JS 구문 + 수동(종료 후 입장→기록+요약, 새 회의 시작 시 이전 viewer 해제, jarvis off→4003).

## 검증
- `.venv/bin/python -m pytest -q` 통과 + `import main, relay_client`.
- `cd jarvis-web && npm run typecheck` 0, viewer.html JS 구문 OK.
- 수동(배포·재시작 후): 회의 진행→종료 → 같은 링크 비번 입장 → 전체 기록 + (잠시 후)요약; 라이브로 보던 탭은 종료 후 요약 패널 갱신; 새 회의 시작하면 이전 종료-열람 탭은 끊겨 재인증; jarvis 끄면 종료 회의 입장 4003.

## 영향 파일
| 파일 | 변경 |
|---|---|
| `relay_client.py` | `_handle_inbound` archive_request 콜백 + `on_archive_request` |
| `main.py` | `_serve_archive`(store 조회·검증·회신) + `_save_meeting` meeting_summary emit |
| `jarvis-web/src/meeting_do.ts` | pendingArchive, archive_request 전달, archive_response 라우팅, meeting_summary broadcast, evict 이동 |
| `jarvis-web/src/types.ts` | archive_request/response·meeting_archive/summary kind + PUBLIC_KINDS |
| `jarvis-web/src/static/viewer.html` | meeting_archive/summary 렌더 + 요약 패널 |
| `tests/test_*` | _serve_archive / relay inbound 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
