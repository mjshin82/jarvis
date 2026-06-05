# 원격 마이크 (Remote Mic) 설계

날짜: 2026-06-05

## 목표

마이크 입력 소스를 추상화한다. 기본은 시스템 마이크지만, 웹 프론트(우선
meeting-web)를 통해 핸드폰·타블렛 등 외부 기기의 마이크 스트림을 입력으로
받을 수 있게 한다.

핵심 통찰 두 가지:

1. **삽입 지점은 백엔드가 아니라 `Microphone`의 블록 소스다.** 실제 마이크+VAD
   경로는 `audio_io.Microphone`이 담당하며 `sounddevice`를 직접 쓴다.
   `AudioBackend.mic_frames()`는 정의돼 있으나 **어디서도 소비되지 않는 미사용
   코드**다. 따라서 백엔드/재생 경로는 건드리지 않고, `Microphone`이 큐에서
   꺼내는 512-샘플 블록의 **공급원만** 교체한다. VAD/wake/utterance 로직은
   불변이다.

2. **전송은 기존 relay를 역방향으로 탄다.** jarvis는 이미 meeting-web Cloudflare
   Worker에 outbound WS(`/publish/<key>`)를 들고 있고 수신 방향은 비어 있다.
   mic 오디오를 그 인프라에 거꾸로 흘려보내면 jarvis 측 인바운드 서버·TLS
   인증서·LAN 제약이 모두 사라진다. Cloudflare가 HTTPS를 처리하므로 브라우저
   `getUserMedia`의 보안 컨텍스트 요건도 충족된다.

## 결정 사항 (확정)

- **A. 입력만(단방향):** 핸드폰 = 마이크. TTS/음악 응답은 로컬 노트북
  스피커에서 재생. 출력 경로(핸드폰으로 TTS 송신)는 **막지 않되 1차 구현 제외** —
  프로토콜에 자리만 예약.
- **A+B. 전환:** 런타임 자동 전환(프레임이 들어오면 remote, 끊기면 local 복귀)
  \+ `/mic` 수동 오버라이드 커맨드.
- **전송:** 웹페이지 + WebSocket. 오디오는 binary 프레임, 제어는 JSON.
- **노출 프론트:** meeting-web(admin 전용). 단 admin 인증/롤 UI는 **추후**. 채널은
  프론트 비종속으로 설계해 향후 `dict-web` 등이 동일 라우트를 재사용한다.

## 아키텍처

### 데이터 흐름

```
[웹 프론트(meeting-web admin)]  getUserMedia → AudioWorklet 16k mono Int16
        │  wss (Cloudflare HTTPS, binary 프레임)
        ▼
[meeting-web Worker / MeetingDO]  key별로 mic-sender → jarvis 수신 소켓으로 binary 포워딩
        │  wss (jarvis가 인증된 인바운드 소켓 보유)
        ▼
[jarvis RemoteMicReceiver] → RemoteMicSource → 512 재청크 → Microphone 큐
        → events()/VAD → utterance → STT → LLM → TTS → 로컬 스피커
```

### jarvis 측 컴포넌트

```
Microphone.events()            VAD/wake/utterance — 불변
        ↑ self._blocks 큐에서 512 블록을 꺼냄
   ┌────┴── MicRouter           활성 소스 선택
   │   auto: remote 프레임 도착 시 remote, idle/끊김 시 local 복귀
   │   manual(/mic): 강제 오버라이드 (auto 무시)
   ├─ LocalMicSource           sd.InputStream (현 _callback 로직 이동) [기본]
   └─ RemoteMicSource          RemoteMicReceiver가 공급하는 프레임 → 512 재청크
```

`MicRouter`는 어느 소스가 활성이든 동일한 `Microphone._blocks` 큐를 채운다.
비활성 소스의 블록은 큐에 넣지 않는다. 소스 전환 시 큐 잔여 블록을 비우고
VAD 상태를 리셋해(기존 pause/resume 패턴) 소스 간 오디오 혼입을 막는다.

#### 단위별 책임

| 단위 | 무엇을 | 의존 |
|------|--------|------|
| `LocalMicSource` | sd.InputStream → 512 float32 블록을 콜백으로 방출. start/stop | sounddevice, config |
| `RemoteMicSource` | 외부에서 받은 Int16 PCM → float32 변환 → 512 재청크 → 블록 방출. idle 타임아웃 감지 | (없음; 프레임을 주입받음) |
| `MicRouter` | 활성 소스 선택, 자동 전환 + 수동 오버라이드, 블록을 `Microphone` 큐로 라우팅 | 두 소스, 큐 |
| `RemoteMicReceiver` | relay에 인증 WS 영속 연결, binary mic 프레임 수신 → `RemoteMicSource`에 주입, 백오프 재연결 | websockets, config, RemoteMicSource |
| `Microphone` | (변경) sounddevice 직결 제거, `MicRouter`가 채우는 큐 소비. VAD/events 불변 | MicRouter, silero-vad |

### relay 측 (meeting-web) 변경

- **새 라우트 `/mic/<key>` (인증):** `/publish`와 동일하게 `Authorization: Bearer
  <RELAY_TOKEN>` 요구. WS. DO에 role="mic"으로 전달.
- **`MeetingDO`:** key별 `micSender` 소켓 슬롯 추가. micSender의 **binary** 프레임을
  해당 key의 jarvis 수신 소켓(아래)으로 그대로 포워딩. micSender의 JSON 제어
  (`mic_start`/`mic_stop`/`level`)도 수신 소켓으로 전달. 수신측 미접속이면 드롭하고
  sender에 `{"kind":"no_receiver"}` 통지. sender 둘이면 last-wins(publisher
  takeover와 동일).
- **jarvis 수신 경로:** jarvis가 mic을 받기 위해 보유하는 인증 인바운드 소켓.
  `/publish/<key>`를 재사용해 DO가 publisher로 binary를 push하거나(현 구조 최소
  변경), 의미 분리를 위해 별도 라우트(`/mic-recv/<key>`)를 둔다. **구현 계획에서
  확정** — 기능 요건은 "key로 페어링된 jarvis에 binary가 도달한다"이다.
- **캡처 페이지:** 최소 토큰-가드 페이지(바닐라 JS, 기존 `meeting.html` 스타일).
  `getUserMedia` → AudioWorklet에서 브라우저 기본 샘플레이트를 16kHz mono로
  다운샘플 → Int16 변환 → binary WS 송신. 연결 상태 / 마이크 켜기 버튼(사용자
  제스처 필요) / 레벨 미터. 출력(TTS 수신) 자리는 sink 스텁만 둔다.

### 프로토콜

기존 relay 메시지는 JSON 텍스트(자막). mic은 다음을 추가한다.

- **binary 프레임:** 16kHz mono Int16 PCM 청크 (헤더 없음, 프레임 전체가 오디오).
- **JSON 제어 프레임:**
  - sender→jarvis: `{"kind":"mic_start"}`, `{"kind":"mic_stop"}`,
    `{"kind":"level","rms":<float>}`
  - relay→sender: `{"kind":"no_receiver"}`
  - **예약(미구현):** jarvis→sender 방향 `{"kind":"voice", ...}` + binary —
    향후 TTS를 프론트로 송신(결정 C). 1차에서는 정의만, 송수신 안 함.

## 1차 범위

**포함:**

- jarvis: `mic_source.py`(`LocalMicSource`, `RemoteMicSource`, 512 재청크,
  `MicRouter`), `remote_mic_receiver.py`(인증 인바운드 WS, 백오프 재연결),
  `audio_io.Microphone` 리팩터, `config.py` 추가(`REMOTE_MIC_ENABLED`,
  `REMOTE_MIC_KEY`, relay URL/token 재사용), `main.py`에 시작 시 mic 페이지 URL
  박스 출력 + `/mic` 커맨드.
- meeting-web: `/mic/<key>` 인증 라우트, `MeetingDO` binary 포워딩, 최소 토큰-가드
  캡처 페이지.

**연기:**

- admin 로그인/롤 UI (지금은 토큰 가드)
- TTS를 프론트로 보내는 출력 경로 (프로토콜 자리만 예약)
- 회의 모드(`/meet`)와의 상호작용 — remote mic는 메인 wake/VAD 흐름 전용
- `dict-web` 등 추가 프론트

## 에러 / 엣지 케이스

- **수신측(jarvis) 미접속**으로 sender가 스트림 → DO 드롭 + sender에
  `no_receiver` 통지.
- **jarvis 수신 소켓 끊김** → 지수 백오프 재연결. 그 사이 `MicRouter`는 로컬로
  폴백.
- **mic 프레임 idle 타임아웃**(sender 종료/네트워크) → 자동 로컬 복귀. 단
  `/mic phone` 강제 중이면 무음 + 콘솔 경고.
- **sender 둘** → last-wins.
- **포맷:** 다운샘플은 브라우저에서 16kHz Int16으로. jarvis는 방어적으로
  변환·클립하고 재청크가 정확히 512 샘플 블록을 방출.
- **에코(결정 A 위험):** 로컬 스피커 → 폰 마이크 되먹임. 기존 `is_speaking()`
  게이트가 부분 완화(자비스 발화 중 VAD 무시). 완전 해결은 결정 B(양방향), **알려진
  한계**로 문서화.
- **회의 모드:** `/meet`는 마이크를 RealtimeSTT에 넘긴다. remote mic는 1차 범위
  밖이며 회의 중에는 비활성.
- **보안:** viewer는 무인증(key가 비밀), mic은 토큰 필요(admin). binary는
  인증된 페어에게만 포워딩. 토큰은 현재 공유 비밀.

## 테스트 전략

- **단위(jarvis):** 재청크(임의 입력 길이 → 정확히 512 블록) · Int16↔float32 변환 ·
  `MicRouter` 전환/오버라이드 우선순위 · `RemoteMicSource` idle 폴백.
- **통합(jarvis):** 가짜 WS가 녹음 WAV를 `RemoteMicReceiver`로 흘려보내 →
  `Microphone`이 기대 utterance를 산출. 폰·Cloudflare 없이 E2E 검증.
- **meeting-web:** DO 포워딩(sender binary → 수신 소켓 도달) · `/mic` 무토큰 거부.
  vitest/wrangler.
- **수동:** 배포된 Cloudflare로 실제 폰 접속(동일/다른 네트워크).

## 영향받는/새 파일

| 파일 | 변경 |
|------|------|
| `mic_source.py` | 신규 — `LocalMicSource`, `RemoteMicSource`, 재청크, `MicRouter` |
| `remote_mic_receiver.py` | 신규 — relay 인바운드 mic 수신 클라이언트 |
| `audio_io.py` | `Microphone` 리팩터 — 블록 소스를 `MicRouter`로 분리 |
| `config.py` | `REMOTE_MIC_ENABLED`, `REMOTE_MIC_KEY` 등 추가 |
| `main.py` | mic URL 박스, `/mic` 커맨드 배선 |
| `meeting-web/src/index.ts` | `/mic/<key>` 라우트 |
| `meeting-web/src/meeting_do.ts` | micSender 슬롯 + binary 포워딩 |
| `meeting-web/src/static/mic.html` | 신규 — 캡처 페이지 |
| `meeting-web/src/types.ts` | mic 제어 메시지 타입 |
