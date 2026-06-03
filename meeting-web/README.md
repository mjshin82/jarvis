# meeting-web

Jarvis 회의 모드(/meet) 자막을 외부 참석자가 브라우저로 볼 수 있게 중계하는 작은 서비스.

- 호스팅: Cloudflare Workers + Durable Objects (회의 키별 인스턴스)
- 프레임워크: Hono + TypeScript
- 클라이언트: 단일 HTML 파일 (외부 의존성 0)

## 동작

```
Jarvis (laptop) ──outbound wss──► Cloudflare Worker ──fan-out──► viewer 브라우저들
                                    └─ MeetingDO (per key) 메모리에 최근 100 이벤트
```

- `GET  /healthz`          상태 확인
- `GET  /m/:key`           viewer HTML
- `GET  /subscribe/:key`   WebSocket viewer (인증 불필요 — 회의 키가 share secret)
- `GET  /publish/:key`     WebSocket publisher (`Authorization: Bearer <RELAY_TOKEN>` 필요)

같은 키로 새 publisher 가 붙으면 기존 publisher 는 `{kind:"kicked"}` 받고 close. 회의 키 충돌이 곧 인수.

## 로컬 개발

```bash
cd meeting-web
npm install
cp .dev.vars.example .dev.vars    # RELAY_TOKEN 설정
npm run dev                       # wrangler dev (localhost:8787)

# 상태 확인
curl http://localhost:8787/healthz   # {"ok":true}

# 자비스 .env
RELAY_URL=ws://localhost:8787
RELAY_TOKEN=devtoken
```

## 배포 (Cloudflare)

```bash
npm run deploy          # = wrangler deploy
wrangler secret put RELAY_TOKEN   # 1회 등록
```

배포 후 URL (예: `https://meeting-web-jarvis.<account>.workers.dev`) 을 자비스 `.env` 의 `RELAY_URL` 에 `wss://...` 형태로.

## 메시지 스키마

`src/types.ts` 참고. 모든 메시지는 JSON 한 줄.

```jsonc
// publisher 가 보내는 형식
{ "kind": "hello",  "meta": { "key":"...","partner":"...","user":"...", ... } }
{ "kind": "source", "text": "Hello, thanks for having me." }
{ "kind": "translation_ko", "text": "안녕하세요…" }
{ "kind": "partial", "text": "...(부분)" }
{ "kind": "end" }

// worker → viewer: ts/seq 부착
{ "ts": 1733267000.123, "seq": 42, "kind": "...", ... }
```

## 한계 (V1)

- DO 가 hibernate 되면 메모리 events 도 사라짐 → 새 viewer 는 빈 채로 시작 (활성 회의 중에만 replay 의미 있음)
- viewer 토큰 없음 — 회의 키 자체가 비밀
- 영구 보관 없음 — 끝난 회의 재시청 X (V2)
