# jarvis-web 채팅 홈 (서브프로젝트 B) 설계

날짜: 2026-06-05

> 상위 비전: jarvis 범용 웹 컨트롤러. 분해 A(골격, 완료) → **B(채팅 홈, 음성 대화)** → C(음성 모드전환).
> 이 문서는 **B** 만 다룬다. A 에서 만든 홈(`/{name}`)의 채팅 placeholder 를 실제 음성 대화로 채운다.

## 목표

jarvis-web 홈(`/{name}`)을 jarvis 와의 **음성 대화 채팅 화면**으로 만든다.
- 입력: A 의 mic-take(웹 마이크 → jarvis) + 기존 호출어("Hey Jarvis").
- 출력(텍스트): jarvis 메인 대화(내 발화 / jarvis 답변)를 웹으로 발행 → 채팅 버블.
- 출력(음성): jarvis TTS 를 웹으로 스트리밍해 **마이크가 있는 곳에서** 재생(헤드셋 모델).

## 데이터 흐름

**입력 (A 재사용, 변경 없음):** 홈 mic-take → `/mic/<key>` → `RemoteMicReceiver` →
`MicRouter`(active=remote) → 메인 wake/VAD → "Hey Jarvis" → STT → LLM → TTS.

**출력 (신규):** jarvis 가 **상시 웹 퍼블리셔**(`/publish/<ROOM_KEY>`) 하나로 발행:
```
speak_response(text):
  web_pub.emit("user", text)                         # 🧑 버블
  for sentence in llm.respond(text):
    web_pub.emit("assistant", sentence)              # 🤖 버블
    wav, sr = tts.synth(sentence)
    if mic.router.active == "remote":
        web_pub.emit_audio(int16(wav), sr)           # 폰에서 재생 / 로컬 skip
        web_speaking_until = now + len(wav)/sr       # 에코 게이트
    else:
        player.enqueue(wav, sr)                      # 로컬 스피커(기존)
      │ DO 가 /subscribe viewer 들에게 JSON + binary broadcast
홈 /subscribe: JSON → 버블 / binary → Web Audio 재생
```

## 퍼블리셔 단일화

`/publish` 슬롯은 DO 당 1개(last-wins). 상시 채팅 퍼블리셔와 회의 RelayClient 가 충돌하므로,
**jarvis 시작 시 RelayClient 1개를 상시 생성**해 채팅 텍스트·TTS 오디오·회의 자막을 모두 그
하나로 보낸다. 회의 전용 RelayClient 생성은 제거하고, 회의는 기존 listener 패턴으로 상시
퍼블리셔에 붙인다(`sess.add_listener(web_pub.emit_async)`).

## jarvis 쪽 변경

**`relay_client.py` (`RelayClient`):**
- 상시 사용 가능: 가벼운 메타(`MeetingMeta(my_name=USER_NAME)` → key=ROOM_KEY)로 시작 시 1개 생성.
- `emit_audio(pcm_bytes, sr)` 추가: outbound 큐에 **bytes** 적재 = `struct.pack("<I", sr) + int16_pcm`
  (앞 4바이트 sr LE + int16 PCM). send 루프가 큐 항목 타입이 str 이면 `ws.send(json)`, bytes 이면
  `ws.send(bytes)`(binary)로 분기.
- 기존 `emit(kind, text)`/`emit_async`/백오프 재연결 유지.

**`main.py`:**
- 시작 시 `RELAY_URL && RELAY_TOKEN` 이면 `web_pub = RelayClient(url, token, MeetingMeta(my_name=
  config.USER_NAME), on_log=console.log)` 생성 + `await web_pub.connect()`. 종료 시 `await web_pub.close()`.
- `web_speaking_until = 0.0` (nonlocal). `mic.events(is_speaking=...)` 인자를
  `lambda: player.is_speaking() or time.monotonic() < web_speaking_until` 로 변경(에코 게이트).
- `speak_response(text)`:
  - `web_pub.emit("user", text)`.
  - 문장마다 `web_pub.emit("assistant", sentence)`.
  - `wav, sr = await tts.synth(sentence)` 후:
    - `mic.router.active == "remote"`: `web_pub.emit_audio((np.clip(wav,-1,1)*32767).astype(np.int16).tobytes(), sr)`,
      `web_speaking_until = max(web_speaking_until, time.monotonic()) + len(wav)/sr`, 로컬 `player.enqueue` **skip**.
    - else: `player.enqueue(wav, sr)` (기존).
  - `web_pub` 가 None(릴레이 미설정)이면 emit 호출은 무시(안전 가드) — 항상 로컬 재생.
- 회의(`_begin_meeting`): per-meeting RelayClient 생성·connect·URL박스 **제거** → `sess.add_listener(web_pub.emit_async)`
  (web_pub 있을 때). 회의 종료 시 listener 정리는 기존 sess.stop 흐름 유지.

## relay (DO) 변경

**`jarvis-web/src/meeting_do.ts` (`MeetingDO`):**
- `attachPublisher` 메시지 핸들러를 분기:
  - `msg.data` 가 **ArrayBuffer(=TTS 오디오)** → `broadcastBinary(data)` (viewer 들에게 raw binary,
    replay 버퍼 미적재).
  - string → 기존 JSON 경로(`handlePublisherMessage`).
- `broadcastBinary(data)` 메서드 추가: `viewers` 각자에게 `ws.send(data)`(실패는 무시).
- 기존 `attachMicSender`/`attachMicReceiver`(원격 마이크), viewer replay, mic_source 그대로.

**`jarvis-web/src/types.ts`:** `EventKind` 에 `"user"`, `"assistant"` 추가.

## 홈 UI 변경 (`jarvis-web/src/static/home.html`)

A 의 홈(로그인 + mic-take + 배지 + nav)은 유지하고 채팅 placeholder 를 실제 채팅으로:
- `<main id="chat">` 를 버블 컨테이너로. CSS: user(우측 정렬), assistant(좌측).
- `/subscribe` WS 에 `binaryType = "arraybuffer"` 설정.
- 메시지 핸들러 확장:
  - **binary** → `playAudio(buf)`: `sr = DataView.getUint32(0,true)`, `pcm = Int16Array(buf,4)`,
    `f32 = pcm/32768`, `audioCtx.createBuffer(1, n, sr)` → `copyToChannel` → `BufferSource` 를
    `playHead`(이전 끝 시각) 이후로 `start` → 순차 재생.
  - JSON `user` → 🧑 버블(우), `assistant` → 🤖 버블(좌, 같은 턴이면 문장 누적), `mic_source` → 배지(기존).
- `AudioContext` 는 자동재생 정책 때문에 **로그인/마이크 토글 제스처에서 생성·`resume()`**.
- 새 메시지 도착 시 자동 스크롤(하단 근처면).

## 에코 처리

- 폰 `getUserMedia({ echoCancellation: true })` (A 에서 이미 적용) — 브라우저 AEC 가 폰 자기 스피커
  에코 제거.
- jarvis `web_speaking_until` 게이트 — 웹 TTS 재생 추정 시간 동안 VAD 억제(FOLLOW_UP 재청취 중
  자기 목소리 자가-입력 방지). 네트워크 지연만큼 근사. 완벽 풀듀플렉스 AEC 는 범위 밖.

## 엣지 케이스

- remote 활성인데 홈 viewer 없음 → 오디오 드롭(보통 홈이 열려 있음). 텍스트는 DO replay 로 복구.
- TTS 라우팅은 문장마다 `router.active` 재평가(응답 중 소스 전환 시 일부 로컬/일부 웹 — 허용).
- `web_pub` 미설정(RELAY 미구성) → 모든 emit 무시, 로컬 재생만(현행 동작).
- 회의 중에도 web_pub 하나로 자막이 흐름(채팅과 동일 채널, 다른 kind).

## 테스트 전략

- **단위(jarvis):** `RelayClient.emit_audio` 가 outbound 에 `struct.pack("<I",sr)+pcm` bytes 적재 ·
  send 루프 str→JSON/bytes→binary 분기 · `speak_response` 라우팅(가짜 web_pub/player/router 주입:
  active=remote → emit_audio 호출+로컬 enqueue 안 함 / local → enqueue, emit_audio 안 함) ·
  `web_speaking_until` 가 emit_audio 시 갱신.
- **통합(jarvis-web):** `mic_relay_check.mjs` 에 publisher→viewer binary 브로드캐스트 검증 추가
  (publisher 가 binary 보내면 viewer 가 isBinary 수신).
- **수동 E2E:** 폰 홈 로그인 → mic-take → "Hey Jarvis, 오늘 날씨" → 🧑/🤖 버블 표시 + 폰에서
  jarvis 음성 재생. 시스템 마이크 모드에선 로컬 스피커로.

## 연기 (C)

- 음성 모드 전환("미팅모드로 변경해줘" → /meet + 웹 navigate).

## 영향 파일

| 파일 | 변경 |
|------|------|
| `relay_client.py` | `emit_audio` + send 루프 bytes 분기 |
| `main.py` | 상시 web_pub 생성, speak_response 텍스트 발행 + TTS 라우팅, is_speaking 에코 게이트, 회의 web_pub 재사용 |
| `jarvis-web/src/meeting_do.ts` | publisher binary → `broadcastBinary` |
| `jarvis-web/src/types.ts` | `user`/`assistant` kind |
| `jarvis-web/src/static/home.html` | 채팅 버블 + Web Audio 재생 |
| `jarvis-web/scripts/mic_relay_check.mjs` | publisher→viewer binary 검증 |
| `tests/` | RelayClient emit_audio, speak_response 라우팅 |
