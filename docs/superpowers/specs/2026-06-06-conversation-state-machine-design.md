# 대화 상태 머신 추출 (ConversationController) 설계

날짜: 2026-06-06

## 목표

jarvis `main()` 에 `nonlocal` 로 흩어진 대화 상태와, 서로 겹치는 4겹 "모드"를
하나의 명시적 상태 머신(`ConversationController`)으로 추출한다. 전환을 단일
경로(`_exit(old) → _enter(new)`)로 모아 **불가능 조합·누락 전이를 구조적으로 강제**한다.

성격: **리팩터 + 불변식 적극 강제**. 외부 동작은 대체로 보존하되, 머신화로
드러나는 잠재 버그(누락 전이 등)는 더 올바른 방향으로 닫는다. asyncio 단일
스레드이므로 락은 불필요(데이터 레이스 없음). 문제는 스레드 안전성이 아니라
논리적 상태 복잡도다.

## 배경 (현재의 부채)

`main()` 은 ~700줄 단일 함수로, 11개 이상의 가변 상태(`state`, `response`,
`watchdog`, `hands_free`, `stop_after_response`, `web_speaking_until`,
`recognizer`, `meeting_session`, `meeting_setup` …)를 ~30개 클로저가
`nonlocal` 로 공유·변이한다. "모드" 개념이 4겹으로 겹치는데 서로 동기화되지 않는다:

- `state` 문자열 머신 (WAITING_WAKE / LISTENING / RESPONDING)
- `MODE.translate` (별도 모듈, `state` 를 안 건드림)
- `meeting_session` / `meeting_setup` (2단계, 역시 `state` 무관, tap 으로 우회)
- `hands_free` / `stop_after_response`
- MicRouter `_mode` / `_active` / `_tap`

회의/번역이 `state` 를 바꾸지 않고 tap 으로 가로채므로 "회의 중 LISTENING" 같은
암묵적 불가능 조합이 코드로 강제되지 않는다. 최근 고친 버그(회의 종료 후 mic
소스 미복원; 마이크 공급 끊김 시 final 미발생으로 멈춤)는 모두 이 **누락 전이**의
증상이다.

## 비범위 (YAGNI)

- 웹 클라이언트 mic 캡슐화(generation 카운터 등) — 별도 작업.
- MicRouter 소스 획득/반납 패턴화(C) — 이번엔 회의 전환 안에서만 snapshot/restore.
- 이벤트 큐 기반 전면 재작성 — 과함.
- `speak_response` 의 LLM/TTS/웹 내부 로직 변경 — 잔류(에코게이트 시계만 위임).

## 아키텍처

### 새 파일 `conversation.py`

- `Mode` (enum): `IDLE · CONVERSING · TRANSLATE · MEETING`
- `ConversationController`: 최상위 모드 + 하위상태 + 전환 로직 소유. 협력자는 주입.

### 상태 (main() nonlocal → 컨트롤러 소유)

`mode`, 대화 `phase`(LISTENING|RESPONDING), `hands_free`, `stop_after_response`,
`response`(Task), `watchdog`(Task), `output_busy_until`(=web_speaking_until 개명),
회의 하위상태(`meeting_session`, `meeting_setup`, `saved_mic_mode`).

### 주입 협력자 (포트) — "무엇"만 알고 "어떻게"는 모름

- `mic` 라우터 (`set_tap` / `set_override` / `snapshot_mode` / `restore_mode`)
- `recognizer` (대화용 스트리밍 STT — tap 대상)
- `speak(text)` async — LLM→TTS→웹 발행; 문장별 `mark_web_speaking(dur)` 호출
- `transcribe(audio)` async, `mode_intent(text)`
- `player` (flush / enqueue_file / is_speaking), `web_pub` (navigate / emit)
- `log`, `set_status`
- `make_meeting()` → MeetingSession, `make_setup()` → MeetingSetup
- `translate_mode` (번역 토글 MODE — `start_translate`/`end_translate`/`is_translate`)
- `fx`(효과음 경로), `follow_up`(config.FOLLOW_UP), `clock`

> 주의: 주입 협력자 `translate_mode`(번역 토글)와 컨트롤러의 `self.mode`(Mode enum)는
> 다른 것. 이름 충돌을 피하려 별칭을 `translate_mode` 로 둔다.

### main() 에 남는 것 (오케스트레이션·배선만)

- 자원 생성/teardown, 비동기 태스크 루프(`audio_loop`, `text_collector`,
  `text_worker`) — 상태 직접 변이 없이 컨트롤러 의도 메서드 호출.
- 콜백 어댑터(`_on_remote_command`, recognizer on_partial/on_final, on_escape,
  슬래시 명령 cmd_ctx) → 컨트롤러 위임.
- `speak_response` 잔류 — `web_speaking_until` 대신 `controller.mark_web_speaking(dur)`.
- 에코게이트 읽기는 `controller.is_output_busy()`.

## Mode 모델 & 전이

```
Mode (상호배타)
├─ IDLE                     호출어 대기
├─ CONVERSING              일반 Q&A 사이클
│   └─ phase: LISTENING | RESPONDING   (+ hands_free, stop_after_response)
├─ TRANSLATE               연속 번역 듣기 루프
└─ MEETING
    └─ phase: SETUP | LIVE  (+ session, saved_mic_mode)
```

### 전환 = `_exit(old) → _enter(new)` (불변식 단일 지점)

| 모드 | `_enter` (setup) | `_exit` (teardown) |
|---|---|---|
| IDLE | tap=None, phase 초기화, 프롬프트 로그 | — |
| CONVERSING·LISTENING | tap=`_feed_recognizer`, watchdog 시작, (cue 시 wake.wav), "듣고 있어요" | response 취소, watchdog 취소 |
| CONVERSING·RESPONDING | response 태스크 생성 | response 취소 |
| TRANSLATE | `translate_mode.start_translate`, tap=None(VAD 경로) | `translate_mode.end_translate`, response 취소 |
| MEETING·SETUP | MeetingSetup 생성 | meeting_setup 정리 |
| MEETING·LIVE | session.start, **mic snapshot**, tap=session.feed, web navigate(meeting) | session.stop, tap=None, **mic restore**, web navigate(home) |

회의 종료 시 mic 소스 복원은 `MEETING._exit` 에 한 번만 적혀 보장된다.

### 파생 동작 (모드에서 계산 — 분산 `if` 제거)

| 모드/phase | tap 대상 | 타임아웃 | 웨이크 수신 |
|---|---|---|---|
| IDLE | None | — | ✓ |
| CONVERSING·LISTENING | `_feed_recognizer` | hands_free 아니면 ✓ | ✓ |
| CONVERSING·RESPONDING | None | — | ✓(중단 후 재청취) |
| TRANSLATE | None (VAD) | 무효 | 무시(/stop 만) |
| MEETING | session.feed | 무효 | 차단(tap 점유) |

### 의도(intent) 메서드 → 전이

| 호출 | 트리거 | 결과 |
|---|---|---|
| `on_wake()` | 호출어 / `/mic` | →CONVERSING·LISTENING (TRANSLATE·MEETING 제외) |
| `on_utterance(audio)` | VAD 발화 | CONVERSING→RESPONDING / TRANSLATE→백그라운드 번역 후 유지 |
| `on_text(line)` | 콘솔·텍스트 | MEETING·SETUP→메타입력 / else→RESPONDING |
| `on_final(text)` | 스트리밍 STT 확정 | LISTENING일 때만 →RESPONDING (아니면 무시) |
| `on_partial(text)` | 스트리밍 STT 조합중 | 상태표시/웹 partial (전이 없음) |
| `on_speech_start()` | VAD 발화 시작 | LISTENING이면 watchdog 취소 |
| `start_listening(hands_free)` | web listen_start | →CONVERSING·LISTENING |
| `stop_listening()` | web listen_stop | RESPONDING이면 stop_after_response=True, else →IDLE |
| `start_translate(lang)` / `stop_translate()` | `/trans` `/stop` | →TRANSLATE / →IDLE |
| `start_meeting()` / `stop_meeting()` | `/meet` `/stop`·web | →MEETING / →IDLE |
| `request_stop()` | Esc | response 취소 → 적절한 복귀 |
| `is_output_busy()` | 에코게이트 조회 | bool (player.is_speaking 또는 web TTS 재생중) |
| `mark_web_speaking(dur)` | speak_response | output_busy_until 갱신 |
| `current_response()` | text_worker | 진행 중 response Task|None |
| `in_meeting()` / `in_meeting_setup()` | cmd_ctx `/stop` 분기 | bool |

### 내부 헬퍼

```
async _transition(new_mode, **kw): await _exit(self.mode); self.mode=new_mode; await _enter(new_mode)
async _enter(m) / _exit(m)         # 위 표
_apply_tap()                       # 모드/phase → tap 대상 계산(한 곳)
_feed_recognizer(block)            # is_output_busy 후 recognizer.feed (에코게이트)
async _respond_audio(audio) / _respond_text(line)  # 기존 respond_flow_*; mode_intent→start/stop_meeting
async _after_response()            # stop_after_response→IDLE / hands_free·follow_up→LISTENING / else IDLE
async listen_timeout()
```

## main() 통합

```
audio_loop:   is_speaking = lambda: player.is_speaking() or controller.is_output_busy()
              "wake"→on_wake() · "utterance"→on_utterance(a) · "start"→on_speech_start()
text_worker:  r=controller.current_response(); (있으면) await r;  await controller.on_text(line)
recognizer:   on_partial→controller.on_partial · on_final→controller.on_final
remote cmd:   kind → 대응 의도 메서드
on_escape:    request_stop
cmd_ctx:      trigger_wake→on_wake, start/stop_translate, start/stop_meeting, in_meeting → 컨트롤러
speak_response: 잔류, web_speaking_until → controller.mark_web_speaking(dur)
```

## 데이터 흐름

```
mic → router → tap(컨트롤러가 모드로 선택: _feed_recognizer | session.feed | None→VAD큐)
VAD → audio_loop → on_wake/on_utterance/on_speech_start
스트리밍STT → on_partial/on_final
웹 control → _on_remote_command → 의도 메서드
콘솔 → text_worker → on_text
                    ↘ 컨트롤러가 speak/transcribe 호출 + _transition 으로 상태 관리
```

## 의도된 동작 변경 (불변식 강제로 바뀌는 부분)

- 모드 전환 시 이전 모드의 teardown 이 **항상** 실행된다. 특히 회의/번역 종료가
  아닌 다른 전환(예: 회의 중 `on_wake` 가 들어올 경로가 생기면)에도 teardown 보장.
- `on_final` 은 LISTENING phase 에서만 응답을 만든다(기존 `state != "LISTENING"`
  가드 유지·강화). 다른 모드의 stray final 무시.
- 새 전이는 진행 중 `response` 를 반드시 취소 → 동시 response 태스크 2개 불가.
- 회의 종료 시 mic 소스 복원은 전환 teardown 에 흡수(현재 손패치를 패턴화).

## 에러 처리

- `MEETING·LIVE` `_enter` 중 예외(session.start 실패): tap 미설정·snapshot 미반영
  상태로 IDLE 로 안전 복귀, 로그. (현재 `_begin_meeting` except 와 동일 의미)
- `_exit` 의 `session.stop()` 예외: 로그 후에도 tap=None·mic restore 는 반드시 수행
  (finally 의미). teardown 의 자원 정리는 예외와 무관하게 보장.
- `transcribe`/`speak` 예외: 응답 흐름에서 잡아 `_after_response()` 로 복귀(영구 멈춤 금지).

## 테스트

fake 협력자: `FakeMic`(set_tap/set_override/snapshot/restore 기록),
`FakeRecognizer`, `FakePlayer`(is_speaking 제어), `FakeWebPub`(emit 기록),
spy `speak`/`transcribe`/`mode_intent`. clock 주입.

1. **전이 정확성**: 각 모드 × 각 의도 → 새 모드 + teardown/setup 부수효과.
   예: MEETING·LIVE 에서 `stop_meeting()` → IDLE 이고 `mic.restore_mode` 가
   snapshot 값으로 호출 + tap=None + web navigate("home").
2. **회귀(겪은 버그 못박기)**:
   - 회의 입장→퇴장 후 mic 모드 복원
   - 응답 중 `stop_listening()` → 끝까지 두고 IDLE (stop_after_response)
   - LISTENING 아닐 때 `on_final` 무시
   - 전이가 이전 response 취소 → 동시 2개 불가
   - TRANSLATE 에서 `on_wake` 무시
3. **파생 동작**: 모드별 `_apply_tap()` 결과, 타임아웃 유효/무효,
   `is_output_busy` 동안 `_feed_recognizer` 차단.
4. 기존 71개 테스트 전부 통과 유지.

수동 E2E 체크리스트: 일반 음성 / 팔로업 / hands_free 토글 / 번역 입·퇴장 /
회의 입·퇴장 / 회의→일반 mic 소스 / Esc 중단.

## 이행 순서 (블래스트 반경 최소화)

1. `conversation.py` + `tests/test_conversation.py` 독립 작성·통과 (main() 무변경).
2. `main()` 을 컨트롤러 사용으로 교체 — nonlocal 제거, 루프/콜백 위임.
   `speak_response` 잔류.
3. 기존+신규 테스트 + 수동 E2E 통과 확인.

실행 방식: 핵심 루프라 subagent-driven-development 로 전이표 단위 태스크 + 리뷰, 각 TDD.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `conversation.py` (신규) | `Mode` enum + `ConversationController` |
| `tests/test_conversation.py` (신규) | 전이/회귀/파생 단위 테스트 |
| `main.py` | nonlocal 제거, 컨트롤러 배선·위임으로 교체; `speak_response` 잔류 |
| `commands.py` | cmd_ctx 키를 컨트롤러 메서드로 연결(시그니처 동일하면 무변경 가능) |
```
