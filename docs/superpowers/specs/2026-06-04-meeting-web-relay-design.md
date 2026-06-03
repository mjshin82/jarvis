# Meeting Web Relay — Design

**Date:** 2026-06-04
**Status:** Draft for review
**Component:** New subproject `meeting-web/` (Cloudflare Workers + Hono + Durable Objects)
+ small Jarvis-side change (`live_translate.py`, `config.py`, `main.py`)

## Context

회의 모드(`/meet`)는 현재 Jarvis 로컬 콘솔에만 자막/번역을 표시한다. 외부 참석자가 같은 자막을 함께 보려면 공유 가능한 웹 페이지가 필요하다. 직전 작업에서 `MeetingSession._emit(kind, text)` 단일 진입점을 마련해 두었기 때문에, 외부 fan-out은 그 자리에 listener 하나 끼우면 된다.

**문제:** Jarvis는 사용자 노트북(NAT 안쪽)에서 동작하고, 회의 참석자는 임의의 위치에서 공용 URL로 자막을 보고 싶다. ngrok 같은 임시 터널 대신 안정적인 호스팅이 필요하다.

**해결 방향:** Jarvis는 outbound WebSocket 으로 서버에 붙고, 참석자는 회의별 URL(`/m/<key>`) 로 들어와 동일 채널을 구독한다. 서버는 Cloudflare Workers + Durable Object 위에서 회의 키 하나당 인스턴스 하나로 메모리에 최근 이벤트를 유지하며 fan-out 한다.

## High-level architecture

```
┌─ Jarvis (Mac, NAT 안쪽) ────────┐
│  MeetingSession._emit          │  outbound wss
│    └─ RelayClient (신규) ──────┼───────────────► ┌─ Cloudflare Worker (Hono) ────┐
└────────────────────────────────┘                 │  POST /publish/:key (ws 업그레이드) │
                                                   │    토큰 검증 → DO 라우팅           │
                                                   │  GET  /m/:key  (HTML)              │
                                                   │  GET  /subscribe/:key (ws)         │
                                                   └────────────────┬───────────────────┘
                                                                    │
                                                          ┌─ MeetingDO (Durable Object) ─┐
                                                          │  publisher socket 1개          │
                                                          │  viewers Set<WebSocket>        │
                                                          │  최근 100개 events (circular)  │
                                                          │  seq 카운터, last_ping_at      │
                                                          └────────────────────────────────┘

브라우저 viewer ──── wss /subscribe/:key ────► 같은 DO 에 연결 → replay + 실시간 push
```

## Repository layout

```
jarvis/
├── meeting-web/                       ← 신규 서브폴더 (Node 생태계)
│   ├── package.json                   ← wrangler, hono
│   ├── wrangler.toml                  ← Cloudflare 설정, DO 바인딩
│   ├── tsconfig.json
│   ├── src/
│   │   ├── index.ts                   ← Hono 앱 (라우팅, 토큰 검증)
│   │   ├── meeting_do.ts              ← Durable Object 구현
│   │   ├── types.ts                   ← 이벤트 스키마
│   │   └── static/
│   │       └── meeting.html           ← 단일 페이지 (CSS/JS 인라인, 의존성 0)
│   ├── README.md                      ← 배포/개발 가이드
│   └── .dev.vars.example              ← 로컬 시크릿 예시
│
├── live_translate.py                  ← (수정) RelayClient 호출 추가
├── relay_client.py                    ← 신규: 자비스용 ws publisher
├── config.py                          ← (수정) RELAY_URL, RELAY_TOKEN
├── main.py                            ← (수정) 회의 시작/종료 시 relay 연결/정리
├── requirements.txt                   ← (수정) `websockets` 추가
└── .env.example                       ← (수정) 예시 키 추가
```

## Wire protocol (Jarvis ↔ Worker ↔ Viewer)

모든 메시지는 JSON. 한 줄 한 메시지(WebSocket text frame).

### Publisher (Jarvis) → Worker → Viewers

```jsonc
// hello (회의 시작 시 publisher 가 첫 메시지로 1회 송신)
{
  "kind": "hello",
  "meta": {
    "key": "Chucklefish_Concode",
    "partner": "Chucklefish",
    "partner_lang": "English",
    "user": "Concode",
    "user_lang": "Korean",
    "started_at": "2026-06-04T14:30:00Z"
  }
}

// 발화/번역 이벤트 (회의 진행 중 N회)
{
  "kind": "source" | "translation_ko" | "translation_en" | "partial" | "info" | "gap",
  "text": "Hello, thanks for having me."
}

// 종료
{ "kind": "end" }
```

Worker 가 각 이벤트에 서버 메타데이터를 덧붙여 viewer 에게 전달:

```jsonc
{
  "ts": 1733267000.123,   // 서버 도착 시각 (epoch sec)
  "seq": 42,              // 채널 내 일련번호 (1부터)
  "kind": "...",
  "text": "...",
  "meta": { ... }          // hello 이벤트에만 포함
}
```

### Viewer → Worker

Viewer 는 클라이언트일 뿐. ws 연결만 유지하고 수신만 한다. (스크롤 등은 클라이언트 로컬)

서버 → viewer 의 첫 N(최대 100) 개 이벤트는 **replay** 로 한 번에 전송, 그 다음부터 실시간 push.

## Authentication

- **Publisher** (`/publish/:key`): HTTP 헤더 `Authorization: Bearer <RELAY_TOKEN>` 필수.
  Worker 가 시크릿(`RELAY_TOKEN`)과 비교, 불일치 시 401.
  자비스에서 `RELAY_TOKEN` 환경변수로 전달.
- **Viewer** (`/subscribe/:key`, `/m/:key`): 인증 불필요.
  회의 키 자체가 share secret. (충분히 unique 한 키 권장 — 이름 충돌 시 자동 덮어쓰기는 아래)

## Conflict & lifecycle (Q12 결정 반영)

- 같은 `key` 로 두 번째 publisher 가 붙으면 **기존 publisher 강제 종료** 후 새 publisher 인수.
  - 옛 publisher 에는 `{"kind":"kicked","reason":"replaced"}` 전송 후 close.
  - viewer 들은 그대로 유지 — 새 publisher 의 이벤트가 이어서 흘러감.
- publisher 가 끊긴 채로 viewer 가 새로 들어오면: 최근 100개 replay 후 새 이벤트 도착할 때까지 대기.
- DO 가 5분 이상 idle (publisher X, viewer X, 새 이벤트 X) → DO storage 도 비우고 자연 만료.

## Replay buffer

DO 의 메모리 안에 **deque (최대 100개)**. publisher 가 보낸 모든 이벤트를 그대로 append (단, `partial` 은 직전 partial 이 있으면 덮어쓰기 — 같은 슬롯 의미). 새 viewer 가 접속하면 deque 를 그대로 한 번에 전송.

DO 가 hibernate (Cloudflare 가 메모리 회수) 되면 deque 도 사라짐. 그래서 V1 은 정말 "활성 회의" 동안만 의미 — 영구 보관은 V2.

## Heartbeat & cleanup

- Worker 가 모든 ws 연결에 30초마다 ping. 60초 응답 없으면 close.
- publisher close → viewer 들에 `{"kind":"publisher_disconnected"}` 통보 (viewer 연결은 유지).
- 마지막 viewer close + publisher 없음 5분 → DO 자체 종료.

## Jarvis side: `relay_client.py`

```python
class RelayClient:
    """회의 모드에서 outbound ws 로 relay 서버에 이벤트를 전송."""
    def __init__(self, url: str, token: str, meta: MeetingMeta, on_error=None):
        ...
    async def connect(self) -> bool:
        # wss://<URL>/publish/<key>, Authorization: Bearer <token>
        # 성공 시 hello 메시지 송신
    async def emit(self, kind: str, text: str = "") -> None:
        # 큐에 적재. 백그라운드 sender 가 한 줄씩 전송.
    async def close(self) -> None:
        # end 송신 후 ws close
```

설계 포인트:
- **non-blocking**: ws 전송 실패가 회의 자체를 막으면 안 됨. 큐 + 백그라운드 sender, 끊김 시 N회 재연결 시도, 실패해도 콘솔 출력은 정상.
- 의존성: `websockets` (이미 httpx 와 결 비슷, 가벼움)

## `MeetingSession` 통합 (live_translate.py)

```python
class MeetingSession:
    def __init__(self, *, log, set_status, llm, meta, ...):
        ...
        self._listeners: list[Callable[[str, str], Awaitable[None]]] = []

    def add_listener(self, cb):
        self._listeners.append(cb)

    def _emit(self, kind, text):
        # 1) 콘솔 출력 (기존)
        # 2) listener 들에 fan-out (await 없음 — fire and forget)
        for cb in self._listeners:
            asyncio.create_task(cb(kind, text))
```

`main.py` 의 `_begin_meeting()` 에서:

```python
if config.RELAY_URL and config.RELAY_TOKEN:
    relay = RelayClient(config.RELAY_URL, config.RELAY_TOKEN, meta=meta)
    if await relay.connect():
        sess.add_listener(relay.emit)
        sess._relay = relay   # /stop 때 close 위해 보관
        console.log(f"🌐 중계 활성: {config.RELAY_URL}/m/{meta.key}")
```

## Config keys (config.py)

```python
RELAY_URL = os.getenv("RELAY_URL", "")           # 예: wss://meeting.example.workers.dev
RELAY_TOKEN = os.getenv("RELAY_TOKEN", "")
RELAY_TIMEOUT_S = float(os.getenv("RELAY_TIMEOUT_S", "5"))
```

키가 비어있으면 relay 활성 안 함 — 콘솔만으로 동작 (현 동작 유지).

## Cloudflare Worker — 라우트 상세

| 경로 | 메서드 | 동작 |
|------|--------|------|
| `/m/:key` | GET | 정적 HTML 반환 (`<script>` 로 같은 키의 `/subscribe/:key` ws 연결) |
| `/publish/:key` | GET (ws upgrade) | 헤더 토큰 검증 → DO 라우팅 → publisher 등록 |
| `/subscribe/:key` | GET (ws upgrade) | DO 라우팅 → viewer 등록, 최근 100개 replay |
| `/healthz` | GET | `{ok:true}` |

Hono 가 ws 업그레이드를 Workers 네이티브 API 로 위임 (`server.upgrade()`).

## Viewer HTML (`meeting.html`)

- 단일 파일, CSS/JS 인라인, 외부 의존성 0
- 화면 구조:
  - 상단 헤더: `🎤 {partner} ↔ {user}` (hello 메타 받으면 채움)
  - 본문: 채팅 식. 같은 발화는 `🧑 원문` + (`🌐` 또는 `🇺🇸`) `번역` 묶음, 묶음 사이 빈 줄
  - 하단: 작은 회색 영역에 현재 `partial` (덮어쓰기, source 오면 비움)
  - 자동 스크롤 하단 (사용자가 위로 스크롤 중이면 잠금)
- 키보드: `t` 글자 크기 토글(기본/크게), `f` 전체화면, `Esc` 잠금 해제

## Files to be created/modified

### Created

- `meeting-web/package.json`
- `meeting-web/wrangler.toml`
- `meeting-web/tsconfig.json`
- `meeting-web/src/index.ts`
- `meeting-web/src/meeting_do.ts`
- `meeting-web/src/types.ts`
- `meeting-web/src/static/meeting.html`
- `meeting-web/README.md`
- `meeting-web/.dev.vars.example`
- `relay_client.py`

### Modified

- `live_translate.py` — `MeetingSession.add_listener` + `_emit` fan-out
- `main.py` — `_begin_meeting` 에서 `RelayClient` 연결, `stop_meeting` 에서 close
- `config.py` — `RELAY_URL` / `RELAY_TOKEN` / `RELAY_TIMEOUT_S`
- `.env.example` — 예시 키 추가
- `requirements.txt` — `websockets`

## Out of scope (V2 후보)

- 회의 영구 보관 / 끝난 회의 다시 보기
- viewer 토큰 / 회의별 접근 제어
- 멀티 publisher (지금은 하나의 자비스만)
- 자비스 → 서버 음성 자체 송신 (현재는 텍스트만)
- 다국어 viewer 토글 (지금은 받은 그대로 표시)

## Verification

### 메뉴얼 E2E

1. **로컬 dev**
   - `cd meeting-web && npm install && npm run dev` (wrangler dev)
   - 출력 URL 메모 (예: `http://localhost:8787`)
   - 자비스 `.env` 에 `RELAY_URL=ws://localhost:8787` + `RELAY_TOKEN=devtoken` (wrangler `.dev.vars` 에 같은 토큰 등록)
   - 자비스 `python main.py` → `/meet` → 메타 입력
   - 브라우저로 `http://localhost:8787/m/<회의키>` 접속 — hello 메타가 헤더에 보이는지 확인
   - 영어/한국어 발화 → viewer 화면에 원문 + 번역이 흐르는지

2. **publisher 충돌**
   - 같은 키로 자비스 두 번 띄움 → 첫 번째가 자동 종료, 두 번째가 이어받음
   - viewer 는 끊기지 않고 새 publisher 이벤트 받음

3. **viewer 재접속**
   - 회의 중 viewer 새로고침 → 최근 100개 replay 후 실시간 이어짐

4. **publisher 끊김**
   - 자비스 강제 종료 → viewer 에 `publisher_disconnected` 통보
   - 5분 후 DO 정리됨

5. **배포**
   - `wrangler deploy` → Cloudflare 워커 URL 받음
   - `.env` 의 `RELAY_URL` 을 `wss://<workers.dev>` 로
   - 외부 참석자에게 `https://<workers.dev>/m/<키>` 공유 → 실시간 자막 보임

### 자동 테스트 (선택)

- 워커 측: `vitest` + `@cloudflare/vitest-pool-workers` 로 DO 단위 테스트 (옵션)
- 자비스 측: `RelayClient` 큐잉/재연결 단위 테스트 (옵션, MVP 에선 생략 가능)

## Costs / limits

- Cloudflare Workers 무료 티어: 일 10만 요청, DO 1M 요청, 128MB 메모리 — 1인 회의 시나리오엔 충분
- WebSocket 연결: 무료 티어에 동시 5K (충분)
- 대역폭: 텍스트 이벤트만 → 회의 1시간에 수십 KB

## Open questions (실행 단계에서 결정)

- viewer 디자인 디테일 (글꼴, 색, 다크모드) — 처음엔 시스템 기본
- 회의 키 공유 시 짧은 URL 필요한가? — V2
- partial 출력을 viewer 에 보일지 토글 — 처음엔 on
