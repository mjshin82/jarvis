# 웹 admin 인증 + mic 토글/소스 표시 설계

날짜: 2026-06-05

## 목표

원격 마이크 스펙([[2026-06-05-remote-mic-design]])에서 미뤘던 "admin 인증 UI"를
구현한다. meeting-web 자막 뷰어(`/m/:key`)에서:

1. **admin 인증** — 별도 `ADMIN_PASSWORD`로 admin 잠금 해제.
2. **mic 토글 버튼** — admin이 버튼을 눌러 이 기기를 jarvis의 마이크로 송출/중단.
3. **소스 표시** — jarvis가 실제로 듣고 있는 소스(시스템/원격)를 뷰어에 실시간 표시.

기존 별도 캡처 페이지(`capture.html`, `/capture/:key`)는 뷰어에 흡수하고 제거한다.

## 인증 모델

권한을 두 비밀로 분리한다.

- **`RELAY_TOKEN`** (기존, 백엔드용): jarvis가 `/publish`(자막 발행)·`/mic-recv`(mic
  수신)에 붙을 때. 변경 없음.
- **`ADMIN_PASSWORD`** (신규 Worker secret, 사람용): 브라우저 admin이 mic을
  송출(`/mic`)할 때.

라우트 가드:

| 라우트 | 가드 | 비고 |
|--------|------|------|
| `/m/:key` | 없음 | 자막 뷰어 (참석자) |
| `/subscribe/:key` | 없음 | 뷰어 WS |
| `/publish/:key` | `RELAY_TOKEN` | jarvis 자막 발행 (기존) |
| `/mic-recv/:key` | `RELAY_TOKEN` | jarvis mic 수신 (백엔드) |
| `/mic/:key` | **`ADMIN_PASSWORD`** | 브라우저 admin mic 송출 (변경) |
| `/capture/:key` | — | **제거** |

- `requireToken` 헬퍼를 둘로 분리: `requireRelayToken(c)`(RELAY_TOKEN 검증) /
  `requireAdmin(c)`(ADMIN_PASSWORD 검증). 둘 다 `Authorization: Bearer` 헤더 또는
  `?token=` 쿼리를 허용한다(브라우저 WS는 커스텀 헤더 불가).
- `Env` 인터페이스에 `ADMIN_PASSWORD: string` 추가. 배포 시
  `wrangler secret put ADMIN_PASSWORD`. 로컬 dev는 `.dev.vars`.

## UI 통합 (`meeting.html`)

기존 자막 뷰어(바닐라 JS, 단일 HTML)에 admin 컨트롤을 얹는다.

**평상시(참석자):** 현재와 동일하게 자막만. 헤더 구석에 🔒 **admin** 토글 하나만 추가.

**admin 잠금 해제:** 🔒 클릭 → 비밀번호 입력(인라인 입력칸) → `ADMIN_PASSWORD`를
`localStorage`에 저장(다음 방문 자동 해제). 비밀번호 정합성은 mic 송출 시 첫 WS
연결로 확인(401이면 안내) — 별도 검증 라운드트립 없음.

**해제 후 컨트롤:**

- 🎙️ **mic 토글**: ON → `getUserMedia` → `ScriptProcessorNode`로 16kHz mono Int16
  다운샘플 → `/mic/:key?token=<ADMIN_PASSWORD>` 바이너리 송출. OFF →
  캡처/소켓 종료. (현 `capture.html` 캡처 로직을 그대로 이식)
- 🎚️ **소스 배지**: jarvis가 지금 듣는 소스(`시스템` / `원격(폰)`)를 실시간 표시.
  내 토글 상태와 독립 — "jarvis 기준 진실"을 보여준다(데이터 흐름은 아래).
- 레벨 미터(현 capture.html과 동일).

**배치:** 헤더에 접이식 admin 영역. 기존 `meeting.html` 스타일·구조를 따른다.
캡처 관련 JS는 `<script>` 내 admin 섹션 한 곳으로 묶어 가독성 유지.

**보안 메모:** `ADMIN_PASSWORD`가 `localStorage`+URL 쿼리에 남는다(기존 토큰과 동일한
토이 트레이드오프). 자막 URL을 참석자와 공유해도 비밀번호 없이는 mic 송출 불가.

## 소스 상태 데이터 흐름 (jarvis → relay → 뷰어)

뷰어가 "jarvis가 듣는 소스"를 알려면 진실의 원천인 jarvis가 자기 상태를 내보내야
한다. 추가 연결 없이 기존 `/mic-recv` 소켓(jarvis↔relay)을 **양방향**으로 쓴다.

```
MicRouter._switch(target)                       # local↔remote 전환 시
   → on_switch 콜백(신규)
   → RemoteMicReceiver 가 /mic-recv 소켓으로 송신:
        {"kind":"mic_source","source":"system"|"remote"}
        │ wss
        ▼
MeetingDO.attachMicReceiver: 메시지 핸들러(신규)
   → lastMicSource 저장 + 모든 viewer 에게 broadcast
        │ (기존 /subscribe 브로드캐스트 경로 재사용)
        ▼
뷰어: mic_source 이벤트 수신 → 소스 배지 갱신
   + 신규 viewer 접속 시 DO 가 lastMicSource 1회 전송 → 늦게 들어와도 현재 상태 표시
```

오디오 binary는 여전히 sender→receiver 단방향이고, 이 제어 경로는 receiver(jarvis)
→viewers 방향이다. 두 방향이 같은 key의 DO 안에서 갈린다.

### 컴포넌트 변경

**jarvis `mic_source.py` — `MicRouter`**
- `__init__(..., on_switch=None)` 추가. `_switch(target)` 끝에서 active가 실제로
  바뀐 경우 `on_switch(self._active)` 호출. 기존 전환/드레인/리셋 로직은 불변, 훅만 추가.
- `_switch`는 항상 asyncio 스레드에서 호출됨(note_remote_activity/check_idle/
  set_override 경로) — 콜백은 동기, 안에서 큐 적재만.

**jarvis `remote_mic_receiver.py` — `RemoteMicReceiver`**
- `/mic-recv` 소켓에서 recv 루프와 **동시에** outbound 전송 가능하게 한다. 작은
  `asyncio.Queue`(`_outbound`)에 상태 JSON을 적재하고, `_connect_once` 안에서
  recv 태스크와 sender 태스크를 함께 돌린다(`asyncio.gather`/`wait`).
- `notify_source(source)` 메서드: `_last_source`에 캐시하고 `_outbound`에
  `{"kind":"mic_source","source":source}` 적재(동기, queue.put_nowait 래핑).
  MicRouter의 `on_switch`로 연결.
- (re)연결 직후 `_last_source`가 있으면 1회 송신해 동기화(끊김 사이 전환을 복구).
  큐는 인스턴스 생명주기 동안 유지.

**jarvis `main.py`**
- `MicRouter` 생성 시 `on_switch`를 receiver의 `notify_source`로 배선
  (receiver 생성 후 `mic.router.on_switch = remote_mic_rx.notify_source` 또는 생성자 주입).
- 시작 URL 박스를 `/capture/<key>` → **`/m/<key>`**(통합 뷰어)로 변경. 문구도
  "원격 마이크 admin" 취지로.

**meeting-web `src/meeting_do.ts` — `MeetingDO`**
- 필드 `lastMicSource: string | null = null`.
- `attachMicReceiver(ws)`에 message 핸들러 추가: JSON 파싱 → `kind === "mic_source"`면
  `lastMicSource = msg.source` + `broadcast(buildEvent({kind:"mic_source", source}))`.
  (바이너리/기타는 무시 — receiver는 평소 수신만 함)
- `attachViewer(ws)`: 기존 replay 후, `lastMicSource`가 있으면
  `safeSend(ws, buildEvent({kind:"mic_source", source:lastMicSource}))` 1회.
- mic_source는 transient라 자막 replay 버퍼(`events`)엔 넣지 않는다 — `lastMicSource`
  필드로만 관리.

**meeting-web `src/types.ts`**
- `EventKind`에 `"mic_source"` 추가. `ClientMessage`/`RelayEvent`에 optional `source?:
  "system" | "remote"` 추가.

**meeting-web `src/index.ts`**
- `requireToken` → `requireRelayToken`/`requireAdmin` 분리. `/mic`은 `requireAdmin`,
  `/mic-recv`는 `requireRelayToken`.
- `/capture/:key` 라우트 + `CAPTURE_HTML` import 제거. `capture.html` 파일 삭제.
- `Env`에 `ADMIN_PASSWORD` 추가.

## 1차 범위

**포함:** 위 컴포넌트 변경 전부 — ADMIN_PASSWORD 인증, 뷰어 통합 mic 토글, jarvis
소스 상태 보고 + 뷰어 배지.

**연기:** 진짜 세션/쿠키 로그인, admin 롤 다중화, mic 외 다른 admin 컨트롤,
TTS→폰 출력.

## 에러 / 엣지 케이스

- **REMOTE_MIC_ENABLED=false**: jarvis가 `/mic-recv` 미연결 → 상태 미전송. 뷰어는 소스
  배지를 숨기거나 기본 "시스템"으로. mic 토글은 동작하나(송출은 됨) jarvis가 안 받음 —
  admin이 jarvis쪽 활성화를 해야 함(문서화).
- **비밀번호 오류**: `/mic` WS 401 → 뷰어가 "admin 인증 실패" 표시 + localStorage 비움.
- **소스 배지 동기화**: 신규 viewer는 DO의 `lastMicSource`로 즉시 현재 상태 수신. jarvis
  미연결이면 lastMicSource=null → 배지 미표시.
- **receiver 재연결**: 끊겼다 붙으면 현재 소스 1회 재송신 → 배지 정합 복구.
- **두 기기 동시 mic**: 기존 last-wins(micSender 교체). 소스 배지는 jarvis 기준이라
  일관.
- **에코(결정 A 한계)**: 기존과 동일, `is_speaking()` 게이트로 부분 완화.

## 테스트 전략

- **단위(jarvis):** `MicRouter.on_switch` 콜백이 전환 시 정확한 source로 호출됨 ·
  `RemoteMicReceiver.notify_source`가 outbound 큐에 적재 · `_handle_message`는 기존대로.
- **통합(jarvis):** 가짜 소켓으로 notify_source → outbound 송신 확인(헤비 네트워크 없이).
- **meeting-web:** `requireAdmin`/`requireRelayToken` 분리 검증(무토큰/오토큰 거부) ·
  `mic_relay_check.mjs` 확장: receiver가 `mic_source` 송신 → viewer가 수신 도달, 신규
  viewer가 `lastMicSource` replay 수신 · `/mic`은 ADMIN_PASSWORD로만 통과.
- **수동 E2E:** 배포 후 `/m/<key>` 접속 → admin 해제 → mic 토글 → jarvis 깨우기 →
  소스 배지가 "원격"으로, 토글 OFF 후 "시스템"으로 복귀 확인.

## 영향받는/새 파일

| 파일 | 변경 |
|------|------|
| `mic_source.py` | `MicRouter.on_switch` 훅 |
| `remote_mic_receiver.py` | 양방향(동시 recv+send), `notify_source`, 재연결 시 상태 송신 |
| `main.py` | on_switch 배선, URL 박스를 `/m/<key>`로 |
| `meeting-web/src/index.ts` | `requireAdmin`/`requireRelayToken` 분리, `/capture` 제거, `Env.ADMIN_PASSWORD` |
| `meeting-web/src/meeting_do.ts` | `lastMicSource`, receiver 메시지 핸들러, viewer 1회 전송 |
| `meeting-web/src/types.ts` | `mic_source` kind + `source` 필드 |
| `meeting-web/src/static/meeting.html` | admin 잠금 + mic 토글 + 소스 배지 + 캡처 로직 이식 |
| `meeting-web/src/static/capture.html` | **삭제** |
| `meeting-web/scripts/mic_relay_check.mjs` | mic_source 흐름 + admin 인증 검증 추가 |
