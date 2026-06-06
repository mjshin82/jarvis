# 회의 ID · 비번 · 기록 저장 설계

날짜: 2026-06-06

## 목표
회의 1건마다 **유니크 ID**를 발급하고, **비번**으로 공개 자막 페이지를 보호하고, 회의 종료 시 전체 기록을 **SQLite**에 저장(+LLM 요약)한다.

1. 회의별 `meeting_id`(생성시간 md5 앞 6자리) → URL `/{room}/meeting/{meeting_id}`.
2. 비번(폼 입력, 빈 값이면 자동 생성) → 해시 보관 → 공개 자막 페이지 입장 시 요구.
3. 종료 시 SQLite 저장: `meeting_id`, 비번 해시, 타이틀, 시작·끝 시간, 원문+번역 트랜스크립트, LLM 요약(백그라운드).

## 핵심 원칙: 키는 두 개
| | 방 키 `config.ROOM_KEY`(="Concode") | `meeting_id`(신규, 회의별) |
|---|---|---|
| 용도 | publish/mic/control 인프라 방 | URL·비번·DB의 회의 1건 식별 |
| 수명 | jarvis 프로세스 | 회의 1건 |

`MeetingMeta.key`는 **변경 없음**(`config.ROOM_KEY` 그대로 → relay/mic/control 유지). `meeting_id`는 **별도 필드**. 모든 연결은 단일 `Concode` Durable Object에 머물고, `meeting_id`+비번해시는 DO가 현재 회의에 대해 들고 있는 **인증 정보**다. (ROOM_KEY 리네이밍은 별도 작업 — 본 스펙 비범위.)

## 비범위 (YAGNI)
- 회의마다 별도 DO 생성(인프라 control/mic가 회의 시작 전 안정 방을 요구 → 불가).
- 비번 평문 DB 저장(해시만). 비번 재설정·만료·재시도 제한(rate limit).
- 트랜스크립트를 요약 외 다른 후처리(검색 인덱싱 등).
- 과거 회의 조회 UI(저장만; 조회는 추후).

---

## 데이터 모델

### MeetingMeta (live_translate.py) — 필드 추가
```
meeting_id: str = ""     # 6자리 hex, 회의 시작 시 발급
password: str = ""       # 평문(폼 입력 또는 자동 생성). 표시·해시용, DB 미저장
started_at: str = ""     # ISO8601, 회의 시작 시각
```
(`key`/title/vocabulary 등 기존 유지.)

### 헬퍼 (live_translate.py 모듈 함수)
- `new_meeting_id() -> str`: `md5(repr(time.time())).hexdigest()[:6]` (소문자 hex). 로컬 유니크면 충분.
- `gen_password() -> str`: `secrets.token_hex(3)` (6자리 hex).
- `hash_password(pw: str) -> str`: `sha256(pw.encode()).hexdigest()`.
- `now_iso() -> str`: `datetime.now().isoformat(timespec="seconds")` (started_at/ended_at/트랜스크립트 ts 공통).

### SQLite 스키마 (meeting_store.py, 파일 `meetings.db`, gitignore)
```sql
CREATE TABLE IF NOT EXISTS meetings (
  id            TEXT PRIMARY KEY,   -- meeting_id
  password_hash TEXT,
  title         TEXT,
  started_at    TEXT,
  ended_at      TEXT,
  transcript    TEXT,               -- JSON: [{ts, source, ko, en}, ...]
  summary       TEXT                -- NULL → 백그라운드 요약 후 UPDATE
);
```

---

## Phase 1 — 회의 ID + 트랜스크립트 + SQLite + 요약 (순수 jarvis)

저위험, 즉시 테스트 가능. 웹은 URL/로그에 `meeting_id`만 노출(게이트 없음).

### live_translate.py
- `MeetingMeta` 필드 + 헬퍼 함수 추가.
- `MeetingSession.start()`: 시작 시 메타 확정 —
  - `if not self.meta.meeting_id: self.meta.meeting_id = new_meeting_id()`
  - `if not self.meta.password: self.meta.password = gen_password()`
  - `self.meta.started_at = now_iso()`
  - 시작 로그에 `meeting_id` 포함.
- **트랜스크립트 누적**: `self._transcript: list[dict] = []`. final 처리 흐름에서 source final 확정 시 `entry = {"ts": now_iso(), "source": text, "ko": "", "en": ""}` 를 append 하고, 그 라인의 번역 결과(`translation_ko`/`translation_en`)를 같은 entry에 채운다(라인-인덱스로 상관). 번역 미사용 모드면 ko/en 빈 채로 둠.
- `record() -> dict`: `{"id", "password_hash"(=hash_password(password)), "title", "started_at", "ended_at"(=now_iso()), "transcript"(=self._transcript)}` 반환. (stop() 호출 후 controller가 읽음.)

### meeting_store.py (신규)
```
class MeetingStore:
    def __init__(self, path="meetings.db"): 연결 + CREATE TABLE.
    def save(self, record: dict): INSERT (transcript → json.dumps). summary NULL.
    def set_summary(self, meeting_id: str, summary: str): UPDATE.
```
sqlite3(표준). 호출은 `asyncio.to_thread` 로 감싸 루프 비차단(작은 쓰기).

### llm.py
- `async def summarize(self, text: str) -> str`: 단일(비스트리밍) completion. 현재 백엔드(client/model/extra) 사용. mock 모드면 `""` 반환. system 프롬프트: "회의 대화를 한국어로 간결히 요약(주요 논의·결정·할 일)".

### conversation.py
- DI 포트 `save_meeting` 추가(콜백, 기본 no-op).
- 회의 teardown(LIVE → meeting_session.stop()) 직후: `record = self.meeting_session.record(); self.save_meeting(record)`. (stop_meeting·모드전환·/bye 등 모든 종료 경로가 `_teardown`을 거치므로 일관 저장.)

### main.py
- `store = MeetingStore("meetings.db")` 생성(프로젝트 루트).
- `save_meeting` 콜백 와이어링: `store.save(record)`(to_thread) → 트랜스크립트 비면 요약 skip, 아니면 백그라운드 태스크 spawn → `summary = await llm.summarize(text)` → `store.set_summary(id, summary)`(to_thread). "먼저 저장 후 백그라운드 요약".
- `_after_meeting_start`: 로그에 `🔑 {meeting_id}` + 자막 URL(`/{ROOM_KEY}/meeting/{meeting_id}`) 포함.

### 테스트
- `tests/test_meeting_store.py`: tmp db에 save → row 확인, set_summary → summary 갱신, transcript JSON 왕복.
- `tests/test_meeting_session.py`: `new_meeting_id` 6자리 hex, `hash_password` sha256 일치, `gen_password` 길이. 주입한 final/번역으로 `record()`의 transcript 구조 확인.
- `tests/test_llm_backend.py`(또는 신규): mock 백엔드 `summarize` 가 `""` 반환·예외 없음.

---

## Phase 2 — 비번 게이트 (jarvis + worker + DO)

### 입력 (비번 폼/프롬프트)
- 웹 회의 폼(app.html `#meeting-form`): 비번 input 추가(제목·워드북 옆). 빈 값 허용.
- 콘솔 `/meet`: MeetingSetup 3번째 단계 `("password", "비번 (Enter=자동 생성)")`. 빈 입력 → 빈 문자열 유지(자동 생성은 session.start()에서).
- control 메시지 `meeting_start` 에 `password` 필드 추가. main.py 가 `MeetingMeta(..., password=...)` 로 전달.

### 인증 정보 → DO (jarvis → publish)
- 회의 시작 시 main `_after_meeting_start`:
  - `web_pub.emit("meeting_creds", json.dumps({"meeting_id":…, "password_hash":hash_password(password)}))` — DO 전용(게이트용).
  - `web_pub.emit("meeting_info", json.dumps({"meeting_id":…, "password":평문}))` — owner 표시용(평문 포함 → 공개 미노출).
  - 콘솔 로그: `🔑 {meeting_id} · 비번 {password} · 자막 {url}`.

### types.ts
- `EventKind` 에 `"meeting_creds"`, `"meeting_info"` 추가. **둘 다 PUBLIC_KINDS 미포함**(공개 viewer 차단).

### meeting_do.ts
- 필드 `currentMeetingId`, `currentPasswordHash`, `lastMeetingInfo` (string|null).
- `handlePublisherMessage`:
  - `meeting_creds`: text JSON 파싱 → `currentMeetingId`/`currentPasswordHash` 저장. **broadcast/append 안 함**.
  - `meeting_info`: `lastMeetingInfo = text` 저장 + broadcast(공개는 PUBLIC_KINDS 필터로 자동 차단 → owner만 수신). append 안 함.
  - `navigate`(home) / `end`: `currentMeetingId=null; currentPasswordHash=null; lastMeetingInfo=null` (회의 종료 정리).
- **watch 게이트**: `watch` role 연결 시 즉시 public viewer 로 붙이지 말고 **대기**. 첫 메시지 `{kind:"auth", mid, pw}` 수신 시 검증 —
  - 라이브 회의 없음(`currentMeetingId==null`) → close(4003, "no meeting").
  - `mid !== currentMeetingId` → close(4003, "bad meeting").
  - `await sha256hex(pw) !== currentPasswordHash` → close(4003, "bad password").
  - 통과 → `attachViewer(ws, "public")`(기존 public 합류).
  - 일정 시간(예: 10s) 내 auth 없으면 close. (sha256: Web Crypto `crypto.subtle.digest`.)
- `attachViewer`(owner): 재접속 동기화에 `if (lastMeetingInfo) safeSend meeting_info` 추가(meeting_title 패턴과 동일).

### index.ts (worker)
- `/:name/meeting/:mid` 라우트 추가 → 기존 `VIEWER_HTML` 동일 서빙(현 `/:name/meeting` 도 유지).
- `/watch/:key` 라우트는 **변경 없음**(key=room=Concode; mid·pw는 auth 메시지로 전달).

### viewer.html
- 자동 연결 대신 **비번 입력 폼** 먼저 표시. URL 경로에서 `room`(첫 세그먼트)·`mid`(`meeting` 다음 세그먼트) 추출.
- 제출 → `/watch/{room}` 연결 → 첫 메시지 `{kind:"auth", mid, pw}` 전송 → 스트림 시작. 소켓이 4003 으로 닫히면 사유 표시(잘못된 비번/회의 없음) + 재입력.

### app.html (owner)
- `meeting_info` handle: 평문 비번·meeting_id 보관 → 회의 헤더에 **공유 줄**(링크 `/{room}/meeting/{mid}` + 비번, 복사 버튼) 표시.

### 테스트
- `npm run typecheck`, app.html/viewer.html JS 구문. DO 게이트는 수동(배포 후): 올바른 비번 통과, 틀린 비번/없는 회의 거부, 종료 후 거부, owner 공유 줄 표시.

---

## 데이터 흐름
입력(폼/프롬프트 비번) → `MeetingMeta(password)` → `start_meeting` → `_begin_meeting` → `MeetingSession.start()`(meeting_id·started_at 발급, password 빈 값이면 생성) → `_after_meeting_start`(creds→DO, info→owner, 로그). 회의 중 final/번역 → `_transcript` 누적. 종료 → `_teardown` → `save_meeting(record())` → SQLite 저장(즉시) → 백그라운드 `llm.summarize` → summary UPDATE. 공개 viewer → 비번 입력 → DO 검증 → 자막 스트림.

## 검증
- `.venv/bin/python -m pytest -q` 통과 + `import main, live_translate, llm, meeting_store, conversation`.
- `cd jarvis-web && npm run typecheck` 0, app.html/viewer.html JS 구문 OK.
- 수동(배포/재시작): /meet·웹폼 비번 입력 → 회의; 콘솔/웹에 meeting_id·비번·링크; 자막 페이지 비번 게이트(정답 통과/오답 거부/종료 후 거부); 종료 후 `meetings.db` 에 행 + 잠시 후 summary 채워짐.

## 영향 파일
| 파일 | Phase | 변경 |
|---|---|---|
| `live_translate.py` | 1+2 | MeetingMeta 필드·헬퍼, start() 메타 확정, 트랜스크립트 누적, record(), 콘솔 비번 단계 |
| `meeting_store.py` (신규) | 1 | SQLite save/set_summary |
| `llm.py` | 1 | summarize() 원샷 |
| `conversation.py` | 1 | save_meeting 포트 + teardown 저장 |
| `main.py` | 1+2 | store 와이어링·백그라운드 요약, creds/info emit·로그, meeting_start 비번 파싱 |
| `jarvis-web/src/types.ts` | 2 | meeting_creds·meeting_info kind |
| `jarvis-web/src/meeting_do.ts` | 2 | creds 저장·watch 게이트·info 동기화 |
| `jarvis-web/src/index.ts` | 2 | `/:name/meeting/:mid` 라우트 |
| `jarvis-web/src/static/viewer.html` | 2 | 비번 입력 + 첫-메시지 인증 |
| `jarvis-web/src/static/app.html` | 2 | 비번 input + meeting_info 공유 줄 |
| `.gitignore` | 1 | meetings.db |
| `tests/test_*` | 1 | meeting_store/id/summary 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
