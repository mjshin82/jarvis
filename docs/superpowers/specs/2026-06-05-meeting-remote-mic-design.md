# 회의 모드에서 원격 마이크 사용 설계

날짜: 2026-06-05

## 목표

`/meet`(회의/번역) 모드에서도 폰(원격) 마이크를 입력으로 쓸 수 있게 한다. 현재
회의 모드는 RealtimeSTT가 물리 시스템 마이크를 직접 잡고, 진입 시 MicRouter를
pause/suppress 하므로 폰 오디오가 무시된다([[2026-06-05-remote-mic-design]] 의
연기 항목이었음).

진입 시 명시적으로 소스를 고른다: `/meet phone` 은 폰, `/meet system`(및 무인자)은
시스템 마이크.

## 배경 (현재 동작)

- `/meet` → `MeetingSession.start()` 가 `AudioToTextRecorder(..., input_device_index=
  mic_idx)` 로 물리 마이크를 직접 캡처(`use_microphone` 기본 True).
- 진입 직전 `mic.pause()` → `MicRouter.pause_local()` (`_suppressed=True`, 로컬 stream
  정지). 그래서 `on_remote_frame` 이 early-return → 폰 프레임 무시.
- 설치된 RealtimeSTT 1.0.2 는 `use_microphone=False` + `feed_audio(chunk,
  original_sample_rate=16000)` 를 지원 → 외부 오디오 주입 가능.

## 명령 & 소스 의미

- **`/meet phone`** → 회의를 폰(원격) 마이크로. RealtimeSTT 를 `use_microphone=False`
  로 만들고, 폰 오디오를 `feed_audio()` 로 먹인다.
- **`/meet system`** → 시스템 물리 마이크(현재 동작).
- **`/meet` (무인자)** → 기본값 **system** (안전, 기존 동작 유지).
- 소스는 **회의 시작 시점에 고정**(회의 중 동적 전환 없음 — RealtimeSTT 는 생성 시
  `use_microphone` 고정).

## 데이터 흐름

평상시(비회의)는 그대로:
```
폰 → RemoteMicReceiver → MicRouter.on_remote_frame → float32 512 재청크 → 메인 큐 → wake/VAD
```

`/meet phone` 중에는 폰 프레임을 메인 VAD 가 아니라 RealtimeSTT 로 우회:
```
폰 → RemoteMicReceiver._handle_message (raw int16 bytes)
       → MicRouter.on_remote_frame
            ├─ tap 설정됨 → tap(raw_bytes) → MeetingSession.feed_remote
            │                               → recorder.feed_audio(raw_bytes, 16000)
            └─ tap 없음 → (기존) suppress 체크 → remote.feed → 메인 큐
```

폰이 보내는 건 이미 16kHz mono Int16 raw 바이트라, 변환 없이 그대로
`feed_audio(raw_bytes, 16000)` 에 넘긴다(float32 재청크는 메인 VAD 전용).

## 컴포넌트 변경

**`mic_source.py` — `MicRouter`**
- `self._tap = None` 필드 + `set_tap(fn)` 메서드(`fn` 은 `(pcm_bytes)->None` 또는 None).
- `on_remote_frame(pcm_bytes)`: 맨 앞에서 `if self._tap is not None: self._tap(pcm_bytes);
  return`. tap 이 없으면 기존 로직(suppress 체크 → note_remote_activity → remote.feed) 불변.
- tap 설정 중엔 메인 큐로 안 가므로 회의 중 메인 wake/VAD 가 폰 소리에 끼어들지 않음.

**`live_translate.py` — `MeetingSession`**
- `__init__(..., use_remote: bool = False)`.
- `start()`: `use_remote` 면 `AudioToTextRecorder(..., use_microphone=False)` (즉
  `input_device_index`/`_pick_physical_mic()` 생략), 아니면 현재대로
  `use_microphone` 기본 True + `input_device_index=mic_idx`.
- `feed_remote(self, pcm_bytes)`: `if self.recorder: self.recorder.feed_audio(pcm_bytes,
  16000)`. (raw int16 bytes 그대로)

**`commands.py` — `/meet`**
- 인자 파싱: `phone`/`remote` → use_remote=True, `system`/`local`/무인자 → False.
- `ctx["start_meeting"](use_remote)` 호출. (현재 `start_meeting`/`start_meeting_setup`
  시그니처에 use_remote 추가)

**`main.py`**
- `start_meeting_setup(use_remote=False)` → `_begin_meeting(setup.meta, use_remote)`.
- `_begin_meeting(meta, use_remote)`:
  - `use_remote and not config.REMOTE_MIC_ENABLED` → 경고("원격 마이크 비활성 —
    REMOTE_MIC_ENABLED") 후 system 으로 폴백(use_remote=False).
  - `MeetingSession(..., use_remote=use_remote)` 생성·start.
  - `use_remote` 면 recorder 시작 후 `mic.router.set_tap(sess.feed_remote)`. 그리고
    원격 비활성/미연결(예: `mic.router._active != "remote"`) 이면
    "⚠ 폰이 연결돼 있지 않습니다 — 폰에서 마이크를 켜세요" 경고(진행은 계속).
  - `mic.pause()` 는 현재대로(메인 흐름 정지).
- `stop_meeting`: 종료 정리에서 **반드시 `mic.router.set_tap(None)`** 후 `mic.resume()`.
  (tap 누락 시 폰 프레임이 메인 큐로 안 돌아옴)

## 엣지 케이스

- **`/meet phone` + 폰 미연결:** 경고 + 진행. recorder 는 무음 대기, 폰을 나중에 켜면
  tap 으로 흐름.
- **REMOTE_MIC_ENABLED=false:** `/meet phone` → 경고 후 system 폴백.
- **회의 중 폰 끊김:** tap 유지, recorder 무음 대기. RemoteMicReceiver 백오프 재연결로
  자동 재개. 동적 소스 전환은 없음.
- **회의 종료:** `set_tap(None)` 보장 → 폰 프레임이 메인 큐로 복귀.
- **이중 처리 방지:** tap 설정 중 메인 큐 미적재 → 회의 wake/VAD 와 충돌 없음.

## 테스트 전략

- **단위 `MicRouter`:** tap 설정 시 `on_remote_frame` 이 tap 으로 raw 바이트 전달 +
  메인 큐 미적재 / `set_tap(None)` 후 기존 경로 복귀.
- **단위 `MeetingSession`:** `use_remote=True` → `feed_remote` 가 가짜 recorder 의
  `feed_audio(bytes, 16000)` 호출. `use_remote=False` → 기존 경로 유지(가짜 recorder
  주입으로 검증, 실제 RealtimeSTT 로드 회피).
- **단위 `/meet` 명령:** `phone`→use_remote=True, `system`/무인자→False, 비활성 시 경고.
- **수동 E2E:** `/meet phone` → 폰에서 말 → 자막 생성. `/meet system` → 시스템 마이크
  자막. 회의 종료 후 평상시 폰 마이크 정상 복귀.

## 연기

- 회의 중 동적 소스 전환(폰↔시스템 실시간).
- 폰으로 자막/TTS 송출.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `mic_source.py` | `MicRouter.set_tap`/`_tap`, `on_remote_frame` 우회 |
| `live_translate.py` | `MeetingSession.use_remote`, `feed_remote`, recorder 분기 |
| `commands.py` | `/meet phone\|system` 인자 |
| `main.py` | `_begin_meeting(use_remote)` 배선, tap 설정/해제, 경고 |
| `tests/` | MicRouter tap, MeetingSession feed, /meet 인자 테스트 |
