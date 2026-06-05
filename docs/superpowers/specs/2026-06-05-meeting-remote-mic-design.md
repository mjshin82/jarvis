# 회의 모드에서 원격 마이크 사용 설계 (동적)

날짜: 2026-06-05

> 갱신: 초기 정적 설계(`/meet phone|system`로 시작 시 고정)를 **동적 설계**로 대체.
> RealtimeSTT 를 항상 `use_microphone=False` 로 두고 MicRouter 의 현재 활성 소스
> 블록을 계속 `feed_audio` 로 흘리면, 회의 도중 폰↔시스템 전환이 자유롭다.

## 목표

`/meet`(회의/번역) 모드에서 입력 소스를 **MicRouter 의 현재 활성 소스**(시스템 또는
폰)로 동적으로 따른다. RealtimeSTT 는 절대 장치를 직접 잡지 않고, jarvis 가
`feed_audio` 로 프레임을 계속 먹인다. 회의 도중 auto-switch 나 `/mic phone|system`
으로 소스를 실시간 전환해도 recorder 재생성·끊김이 없다. `/meet` 는 소스 인자를
받지 않는다.

## 배경

- RealtimeSTT 1.0.2: `use_microphone=False` + `feed_audio(chunk, original_sample_rate=
  16000)` 지원.
- **`feed_audio` 포맷 주의:** `chunk` 가 numpy 면 내부에서 `chunk.astype(np.int16)`
  → bytes. 우리 내부 블록은 float32 [-1,1] 라 그대로 주면 0(무음)으로 잘린다.
  따라서 **`(block*32767).astype(int16).tobytes()` 로 변환해 bytes 로 먹인다**(bytes 는
  버퍼에 그대로 적재됨).
- `MicRouter` 는 이미 local/remote 소스를 추상화하고 auto-switch + `/mic` 오버라이드를
  제공한다. 두 소스 모두 sink 로 **16kHz mono float32 512-샘플 블록**을 방출한다.

## 데이터 흐름

평상시(비회의): `활성 소스 → MicRouter._sink → 메인 큐 → Microphone.events() → wake/VAD`.

회의 중: 메인 큐 대신 **회의 tap** 으로 우회.
```
활성 소스(local sounddevice 또는 remote 폰)
   → MicRouter._sink_{local,remote} (active 소스만)
        ├─ tap 설정됨 → tap(float32 512 block) → MeetingSession.feed_block
        │                                        → (block*32767).int16.tobytes()
        │                                        → recorder.feed_audio(bytes, 16000)
        └─ tap 없음 → 메인 큐 (wake/VAD)
```
- tap 은 **활성 소스의 블록**을 받는다 → 폰이면 폰 블록, 시스템이면 시스템 블록.
- 회의 도중 소스 전환(auto-switch / `/mic`)은 그냥 어느 소스가 active 냐만 바뀜 →
  tap 으로 흐르는 프레임이 자동으로 바뀐다. recorder 는 그대로.
- tap 이 설정된 동안 메인 큐로 안 가므로 회의 중 wake/VAD 가 안 끼어든다.

## 컴포넌트 변경 (정적 설계 대비)

**`mic_source.py` — `MicRouter`**
- tap 을 **블록 레벨**로 이동: `_sink_local`/`_sink_remote` 에서 해당 소스가 active 이고
  `_tap` 이 설정돼 있으면 블록을 tap 으로 보내고 return(큐 미적재). 아니면 기존대로 큐.
- `on_remote_frame` 은 tap 분기 제거 → 원래대로(suppress 체크 → note_remote_activity →
  remote.feed). (회의 중엔 pause/suppress 안 함 — 아래 main 참고)
- `set_tap(fn)` / `active` 프로퍼티는 유지.

**`live_translate.py` — `MeetingSession`**
- `use_remote` 파라미터 **제거**. recorder 는 **항상 `use_microphone=False`**(장치
  미점유, `input_device_index`/`_pick_physical_mic` 미사용).
- `feed_block(self, block)`: float32 512 블록 → `(np.clip(block,-1,1)*32767).astype(
  np.int16).tobytes()` → `recorder.feed_audio(bytes, 16000)`. recorder 없으면 무시.
- `_pick_physical_mic()` 및 그 전용 `pyaudio` import 제거(더 이상 안 씀).

**`commands.py` — `/meet`**
- `phone`/`system` 인자 **제거**. `/meet` → `start_meeting()` (무인자).

**`main.py`**
- `_begin_meeting(meta)` (use_remote 없음). `MeetingSession()` 생성·start 후
  `mic.router.set_tap(sess.feed_block)`.
- **`mic.pause()`/`resume()` 호출 제거** — 회의 중에도 LocalMicSource 가 계속 캡처해야
  tap 으로 시스템 마이크를 흘릴 수 있다. tap 이 블록을 큐에서 가로채므로 메인 wake/VAD 는
  자연히 idle(큐 빔). RealtimeSTT 가 장치를 안 잡으니 장치 충돌도 없다.
- `stop_meeting`: 정리에서 `mic.router.set_tap(None)` (큐 경로 복귀). resume 불필요.
- REMOTE_MIC_ENABLED 게이팅·"폰 미연결" 경고 제거 — 폰이 없으면 LocalMicSource(시스템)가
  자동으로 feed 된다. (어느 소스인지 알고 싶으면 기존 `🎙️ 입력 소스 →` 전환 로그로 보임)

## 엣지 케이스

- **폰 없음:** active=local → 시스템 마이크 블록이 feed → 회의 자막 정상.
- **회의 중 폰 켜기:** auto-switch(또는 `/mic phone`) → active=remote → 폰 블록이 feed.
  recorder 그대로, 끊김 없음.
- **회의 중 폰 끄기/idle:** auto-switch 로 active=local 복귀 → 시스템 마이크로 자동 복귀.
- **회의 종료:** `set_tap(None)` → 블록이 메인 큐로 복귀 → wake/VAD 재개.
- **소스 전환 시 블록 경계:** 전환 순간 `_switch` 가 큐를 비우고 remote 버퍼를 reset →
  tap 으로는 새 소스 블록만 흐름(혼입 없음).

## 테스트 전략

- **단위 `MicRouter`:** tap 설정 시 active 소스의 블록이 tap 으로 가고 큐 미적재
  (local active → _sink_local 블록이 tap 으로 / remote active → _sink_remote 블록이 tap 으로).
  tap 해제 시 큐로 복귀. 비활성 소스 블록은 어느 경우든 무시.
- **단위 `MeetingSession`:** `feed_block(float32 블록)` 이 가짜 recorder 의
  `feed_audio` 를 int16 bytes + 16000 으로 호출(스케일 검증: 0.5 → ~16383). recorder
  None 이면 무시. recorder 가 항상 `use_microphone=False` 로 생성됨(가짜 주입 검증).
- **단위 `/meet`:** 무인자로 `start_meeting()` 호출(인자 없음).
- **수동 E2E:** `/meet` → 시스템 마이크로 자막. 회의 중 `/mic phone`(또는 폰 켜기) →
  자막 소스가 폰으로 전환(끊김 없이). `/mic system` 으로 복귀. `/stop` 후 평상시 정상.

## 연기

- 폰으로 자막/TTS 송출.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `mic_source.py` | tap 을 `_sink_local`/`_sink_remote` 블록 레벨로, `on_remote_frame` 원복 |
| `live_translate.py` | `use_remote` 제거, 항상 `use_microphone=False`, `feed_block`(float32→int16), `_pick_physical_mic`/pyaudio 제거 |
| `commands.py` | `/meet` 인자 제거 |
| `main.py` | `_begin_meeting(meta)` tap 설정/해제, pause/resume·경고 제거 |
| `tests/` | MicRouter 블록 tap, MeetingSession feed_block, /meet 무인자 |
