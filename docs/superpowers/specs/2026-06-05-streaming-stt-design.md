# 일반 대화 스트리밍 STT 설계

날짜: 2026-06-05

> 상위 비전: jarvis 범용 웹 컨트롤러. 회의 모드의 실시간 STT(부분결과)를 **일반 대화**(호출어
> 이후)에도 확대해, 조합중("📝") 텍스트를 콘솔·웹에 실시간 표시한다.
> (요청 제목의 "RealtimeTTS" 는 음성 **인식**(STT) 맥락 — RealtimeSTT 로 해석.)

## 문제 / 목표

현재 일반 대화: 호출어("Hey Jarvis") → silero VAD 가 발화 **전체**를 버퍼링 → 배치
`stt.transcribe`(faster-whisper) → 응답. 캡처가 끝난 뒤에야 텍스트가 나와 "조합중" 표시가 불가능하다.

회의 모드는 이미 `RealtimeSTT` 를 연속 피드해 partial 콜백으로 조합중을 낸다. 그 패턴을 일반
대화에 가져와, **호출어 이후 말하는 동안 조합중 텍스트가 콘솔과 웹 홈 채팅에 실시간 표시**되게 한다.

## 결정 (사용자 승인)

- **STT 엔진 통일(A):** 호출어 이후 일반 대화도 RealtimeSTT 로 인식(partial + final 동일 엔진).
  faster-whisper(`stt.py`)는 **번역 모드 전용**으로 유지하고 일반 대화 경로에서만 은퇴.
- **표시:** 웹 홈 채팅 + 콘솔 둘 다.

## 아키텍처

### 청취 흐름 재구성 (`main.py`)

상태 기계를 회의 패턴으로:
- **WAITING_WAKE**: 기존대로 `mic.events` 가 openWakeWord 로 호출어 감지(탭 없음).
- **호출어 감지 → LISTENING**: `mic.router.set_tap(recognizer.feed_block)` 으로 마이크 블록을
  `StreamingRecognizer` 로 연속 피드 + `ok.wav`. (탭이 걸리면 `mic.events` 는 블록을 받지 않아
  청취 중 wake/VAD 와 충돌 없음 — 회의 모드와 동일.)
- **partial**: recognizer 의 실시간 콜백 → 조합중 텍스트를 콘솔 status + 웹으로 스트리밍.
- **final**: recognizer 의 자체 VAD(post_speech_silence_duration)가 발화 끝을 판정 → 최종 텍스트.
- **final → RESPONDING**: 탭 해제 후 최종 텍스트로 기존 처리 — `mode_intent` 검사 후 매칭 시
  `_handle_mode`, 아니면 `speak_response(text)`(= `emit("user")` + LLM + `emit("assistant")` + TTS).
- **FOLLOW_UP**: 다시 LISTENING(재탭). **LISTEN_TIMEOUT_S** 안에 final 없으면 탭 해제 + `idle()`.

end-of-speech 판정이 silero(SILENCE_MS) → RealtimeSTT 로 바뀐다(의도된 변경).

### 신규 컴포넌트 `streaming_stt.py` — `StreamingRecognizer`

회의 `MeetingSession` 의 인식부를 본뜬 **번역 없는** 경량 래퍼. 회의 통합(공유)은 안전상 비범위.

- 생성자: `StreamingRecognizer(*, on_partial, on_final, model=config.MEET_STT_MODEL,
  realtime_model=config.MEET_STT_REALTIME_MODEL, language=config.WHISPER_LANG, on_log=print)`.
- 내부 `AudioToTextRecorder(use_microphone=False, enable_realtime_transcription=True,
  on_realtime_transcription_update=<partial>, model=..., realtime_model_type=...,
  language=..., spinner=False, post_speech_silence_duration=0.7, silero_sensitivity=0.4,
  webrtc_sensitivity=3, device="cpu", compute_type="int8", level=30)` — 회의와 동일 설정.
- `feed_block(block: np.ndarray)`: float32 → int16 PCM bytes → `recorder.feed_audio(pcm16, 16000)`
  (회의 `feed_block` 과 동일 변환).
- partial 콜백(recorder 스레드) → `loop.call_soon_threadsafe` 로 `on_partial(text)` 안전 디스패치
  (직전과 동일하면 skip — dedup).
- 백그라운드 final 루프: `await asyncio.to_thread(recorder.text, _final_cb)` 반복 → 큐 → `on_final(text)`.
- `start()`(백그라운드 태스크 기동) / `close()`(정리). **메인 시작 시 1회 생성·start** 해 상시 대기
  (첫 호출어 지연 방지). RealtimeSTT 미설치/로드 실패 시 graceful: 일반 STT 로 폴백(아래 엣지).

### 표시 — 콘솔 + 웹

- **콘솔**: partial → `console.set_status(f"📝 {text}")`(회의와 동일). final 은 기존 `speak_response`
  의 `console.log(f"🧑 {text}")`.
- **웹 emit**: 일반 partial → `web_pub.emit("partial", text)`(web_pub 있을 때). final 확정은 기존
  `speak_response` 의 `web_pub.emit("user", text)` 가 담당.
- **웹 `app.html`(홈 채팅)**: `partial` 핸들러를 **뷰 인식**으로 변경 —
  - 회의 뷰(`data-view==="meeting"`): 기존 `#log` draft 카드(변경 없음).
  - 홈 뷰: `#chat` 에 조합중 user 버블(`.bubble.user.draft` — 흐림 + 깜빡임 커서) 생성·갱신.
  - `user` 이벤트 도착 시: 홈 채팅 draft 버블이 있으면 그것을 최종 텍스트로 확정(`.draft` 제거),
    없으면 기존 `addText("user", ...)`. → 중복 버블 없이 조합중→확정 전환.

## 데이터 흐름

```
WAITING_WAKE: mic.events → "wake"
  → LISTENING: mic.router.set_tap(recognizer.feed_block); ok.wav
      block → recognizer.feed_block
        → on_partial(text): console.set_status("📝 …"); web_pub.emit("partial", text)
        → on_final(text):   mic.router.set_tap(None); RESPONDING
              mode_intent(text) ? _handle_mode : speak_response(text)
                speak_response → emit("user", text)=draft 확정 → LLM → emit("assistant")+TTS
  → FOLLOW_UP ? 재-LISTENING : idle
  LISTEN_TIMEOUT_S 안에 final 없음 → set_tap(None) + idle
```

## 엣지 케이스

- **원격 마이크(폰)**: 탭이 `on_remote_frame` 경로의 블록도 받으므로 그대로 스트리밍됨.
- **메모리**: RealtimeSTT(tiny+small) + faster-whisper(small) 동시 로드 — 번역 모드 유지를 위해 수용(토이).
- **RealtimeSTT 미설치/로드 실패**: `StreamingRecognizer.start()` 가 graceful 비활성 →
  일반 대화는 기존 배치 `stt.transcribe` 경로로 폴백(현행 동작 보존). 즉 스트리밍은 best-effort 향상.
- **번역 모드**: 변경 없음 — 여전히 `_translate_bg` + faster-whisper.
- **회의 모드**: 변경 없음 — 별도 `MeetingSession`. 일반 recognizer 와 동시에 탭을 잡지 않음(상호 배타 상태).
- **partial 중 호출어 재트리거**: LISTENING 중엔 탭으로 mic.events 가 블록을 안 받아 wake 미발생(정상).
- **웹: partial 후 user 안 옴(무음/오인식)**: draft 버블이 남을 수 있음 → LISTEN_TIMEOUT/다음 발화 시
  정리. 단순화를 위해 다음 partial 이 같은 draft 를 갱신, gap 성격의 정리는 비범위(토이 허용).

## 테스트 전략

- **단위(jarvis):** `StreamingRecognizer` — (1) `feed_block` 이 float32 블록을 int16 PCM 으로 변환해
  주입(가짜 recorder 의 `feed_audio` 인자 검증), (2) partial 콜백 → `on_partial` 디스패치 + dedup,
  (3) final 디스패치 → `on_final`. RealtimeSTT 라이브러리는 가짜(stub recorder) 주입으로 대체
  (생성자가 recorder 팩토리/주입을 허용하도록 설계 — 테스트 용이성).
- **웹(jarvis-web):** `app.html` 인라인 JS `node --check`; 라우트 동일성 기존 체크 유지.
- **수동 E2E:** "Hey Jarvis" → 말하는 동안 콘솔 status `📝 …` + 웹 홈 채팅에 조합중 버블 실시간 →
  말 끝나면 user 버블로 확정 + jarvis 응답. 폰 원격 마이크에서도 동일.

## 비범위 / 연기

- 회의 `MeetingSession` 과 `StreamingRecognizer` 의 공유/통합(중복 최소, 안전 우선).
- faster-whisper 완전 제거(번역 모드가 사용).
- 조합중 버블의 정교한 취소/gap 정리.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `streaming_stt.py` (신규) | `StreamingRecognizer` — RealtimeSTT 래퍼(번역 없음), feed_block/partial/final |
| `main.py` | 청취 흐름 RealtimeSTT-driven 화(LISTENING tap→recognizer, on_final→응답), recognizer 생성·배선·종료, partial→web/console |
| `jarvis-web/src/static/app.html` | `partial` 뷰 인식(홈 채팅 조합중 버블) + `user` 확정 전환 |
| `tests/test_streaming_stt.py` (신규) | `StreamingRecognizer` 변환·콜백 단위 테스트 |
