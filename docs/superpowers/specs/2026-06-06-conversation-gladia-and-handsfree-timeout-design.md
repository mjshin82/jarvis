# 일반 대화 Gladia STT 옵션 + 핸즈프리 30초 타임아웃 설계

날짜: 2026-06-06

## 목표
- **Part A**: 일반 대화(평상시) STT 백엔드로 **Gladia** 를 선택할 수 있는 옵션 추가
  (기본은 기존 로컬 RealtimeSTT). Gladia 는 클라우드 과금이라 **듣는 동안만 연결**.
- **Part B**: 웹 핸즈프리 청취에 **30초 무발화 타임아웃** — 초과 시 대기 복귀 + **웹 마이크
  해제(얼럿 없이 버튼만 전환)**. LLM/TTS 진행 중에는 카운트하지 않고, 입력 대기(LISTENING)
  에서만 카운트.

성격: 기능 추가. 외부 동작은 보존하되(A 기본 local = 현 동작), 새 옵션/타임아웃을 더한다.

## 비범위 (YAGNI)
- 미팅 STT 로직(`live_translate`/Gladia 미팅 경로) 변경 없음 — 미팅은 기존 `stt_backend` 그대로.
- 로컬(RealtimeSTT)의 상시-가동 모델은 유지(부팅 프리로드).
- 마이크 권한 자체를 브라우저가 영구 회수하는 건 불가 — jarvis 가 웹에 "해제 신호"를 보내고
  웹이 getUserMedia 트랙을 stop 한다(다음 탭이면 재요청).

---

## Part A — 일반 대화 STT 에 Gladia 옵션

### 난점과 방향
- 로컬 RealtimeSTT: 상시 가동(무료) + 듣는 동안만 feed.
- Gladia: 연결 중 과금 → **LISTENING 진입 시 연결, IDLE 시 해제**. 듣기↔응답 사이클
  동안은 유지(매 턴 재연결 방지), 30초 타임아웃/종료(=IDLE)에서만 해제.

### 신규 `conversation_stt.py` — `ConversationSTT` (facade, DI)
컨트롤러에 단일 인터페이스 제공, backend 수명주기 차이를 내부 흡수.
- 생성자: `make_local()`(→RealtimeSTTAdapter), `make_gladia()`(→GladiaSTT),
  `settings_get`(=settings.get), `on_log=print`.
  - `make_local`/`make_gladia` 는 각각 `on_partial`/`on_final` 이 이미 바인딩된 backend 를 반환
    (main 이 controller.on_partial/on_final 로 구성).
- 상태: `_local`(지연 생성·상시 유지), `_active`(현재 backend 또는 None).
- `feed_block(block)`: `_active` 가 있으면 그쪽으로 라우팅, 없으면 무시.
- `async start()`(부팅): `settings_get("conversation_stt_backend")=="local"` 이면 로컬 생성+start
  (모델 프리로드). gladia 면 지연(아무 것도 안 함).
- `async resume()`(LISTENING 진입): 설정 읽어
  - `"gladia"`: `make_gladia()` 새로 생성→`await start()`(연결), `_active=그것`.
  - `"local"`: 로컬 ensure(없으면 생성+start), `_active=로컬`.
  - 이미 같은 backend 가 active 면 그대로 둠(중복 연결 방지).
- `async suspend()`(IDLE 진입): `_active` 가 **gladia** 면 `await close()` 후 `_active=None`;
  local 이면 no-op(상시 유지, `_active` 유지).
- `async aclose()`(종료): 로컬·active 모두 정리.

> 라이브 스위치: 설정을 바꾸면 다음 `resume()`(다음 듣기)부터 반영. 미팅 중/응답 중 변경은 다음 듣기부터.

### `GladiaSTT` (gladia_stt.py)
- `feed_block(block)` 추가: `from realtime_stt import to_pcm16` 후 `self.feed_pcm(to_pcm16(block))`.
  start/close/on_partial/on_final 기존 그대로 → RealtimeSTTAdapter 와 동일 인터페이스(start/close/feed_block/on_*).

### 컨트롤러 (conversation.py)
- 생성자 인자 `recognizer` 는 이제 `ConversationSTT`(또는 None). 기존 `_feed_recognizer`/`_apply_tap` 의
  `recognizer is not None` 가드 유지.
- `_to_listening`: `_apply_tap()`/cue 전에 `if self.recognizer is not None: await self.recognizer.resume()`.
- `_set_idle`: teardown 후 `if self.recognizer is not None: await self.recognizer.suspend()`.
- 종료 정리(main finally): `await recognizer.aclose()`(현재 `recognizer.close()` 대체).
- `_feed_recognizer`(에코게이트) 그대로 — `recognizer.feed_block`.

### settings.py
- `DEFAULTS["conversation_stt_backend"] = "local"`, `ALLOWED["conversation_stt_backend"] = {"gladia","local"}`.

### main.py
- recognizer 구성 교체:
  ```python
  from conversation_stt import ConversationSTT
  def _make_local():
      return StreamingRecognizer(on_partial=lambda t: controller.on_partial(t),
                                 on_final=lambda t: controller.on_final(t),
                                 model=config.MEET_STT_MODEL, realtime_model=config.MEET_STT_REALTIME_MODEL,
                                 language=config.WHISPER_LANG, on_log=console.log)
  def _make_gladia():
      from gladia_stt import GladiaSTT
      langs = [s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()]
      return GladiaSTT(config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
                       on_partial=lambda t: controller.on_partial(t),
                       on_final=lambda t: controller.on_final(t), on_log=console.log)
  recognizer = ConversationSTT(make_local=_make_local, make_gladia=_make_gladia,
                               settings_get=settings.get, on_log=console.log)
  ```
  - 단, `controller` 가 recognizer 보다 먼저 필요(콜백). 현행처럼 lambda 가 controller 를 늦게 바인딩하도록 구성
    (recognizer 는 controller 생성 후 만들거나, lambda 지연참조). **순서**: recognizer=ConversationSTT(...) 를
    controller 생성 **후** 만들고, controller 에 주입하는 대신 controller.recognizer 를 set 하는 방식 회피를 위해
    — 기존(Task7)처럼 lambda 늦바인딩 사용: ConversationSTT 의 make_* 가 호출되는 시점(resume, 부팅 start)은 모두
    controller 존재 이후이므로 안전. recognizer 인스턴스 자체는 controller 생성자 인자로 필요하므로,
    **recognizer 를 먼저 만들고**(make_* 는 호출 안 됨, 지연) controller 에 넘긴다. make_local/make_gladia 내부의
    `controller` 참조는 호출 시점(부팅 start/resume) 해석 → controller 존재. ✓
  - 부팅: `await recognizer.start()`(현행 위치, control_rx/recognizer start 묶음 자리)에서 ConversationSTT.start().
- `from streaming_stt import StreamingRecognizer` 유지.

### 웹 설정 모달 (app.html)
- "일반 대화 STT: Gladia / 로컬" 라디오 1행 추가(name `set-conv-stt`, value `gladia`/`local`).
- `fillSettings(s)`: `set-conv-stt` 를 `s.conversation_stt_backend` 로 체크.
- `curSettings()`: `conversation_stt_backend` 포함(기본 `"local"`).

### 테스트 `tests/test_conversation_stt.py` (신규)
fake local/gladia(start/close/feed_block 기록) + settings_get 주입:
- `resume()` 가 설정대로 backend 선택(gladia→gladia 생성+start, local→local).
- `suspend()` 가 gladia 만 close(local 유지).
- 설정 라이브 스위치: local→gladia 변경 후 resume 시 gladia.
- `feed_block` 이 active 로 라우팅(active 없으면 무시).
- `aclose()` 정리.

---

## Part B — 핸즈프리 30초 타임아웃 + 웹 마이크 해제

### config.py
- `HANDS_FREE_TIMEOUT_S = float(os.getenv("HANDS_FREE_TIMEOUT_S", "30.0"))`.

### 컨트롤러 (conversation.py)
- 생성자 인자 `hands_free_timeout_s`(=config.HANDS_FREE_TIMEOUT_S) 추가.
- `_listen_timeout` 교체:
  ```python
  async def _listen_timeout(self):
      timeout = self.hands_free_timeout_s if self.hands_free else self.listen_timeout_s
      try:
          await asyncio.sleep(timeout)
      except asyncio.CancelledError:
          return
      if not (self.mode is Mode.CONVERSING and self.phase is Phase.LISTENING):
          return
      if self.hands_free and self.web_pub is not None:
          self.web_pub.emit("mic_release")      # 웹에 마이크 해제 신호
      self.log("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
      await self._set_idle()
  ```
  - hands_free(웹) → 30초, 일반(로컬) → 8초(`listen_timeout_s`). 기존 `if self.hands_free: return` 제거.
  - watchdog 은 LISTENING 에서만 armed, 발화 시작/응답 진입 시 취소 → LLM/TTS 중엔 카운트 안 됨(불변).

### relay 이벤트 `mic_release` (owner 전용)
- `jarvis-web/src/types.ts`: `EventKind` 에 `"mic_release"` 추가. **PUBLIC_KINDS 미포함**.
- `jarvis-web/src/meeting_do.ts` `handlePublisherMessage`: `navigate` 처럼 **append 없이 broadcast**
  하는 케이스 추가(리플레이 버퍼 미적재):
  ```typescript
  if (msg.kind === "mic_release") {
    this.broadcast(this.buildEvent(msg));
    return;
  }
  ```

### 웹 (app.html) handle()
- 케이스 추가:
  ```javascript
  case "mic_release":
    voiceOn = false;
    $("voice-toggle").classList.remove("active");
    mic.apply();      // 의도적 중단(gen 가드 → onLost 미발화 → 얼럿 없음)
    return;
  ```
- 얼럿 없음: `mic.apply()`→`_stop` 은 `onLost` 를 안 탐. (kicked/closed 도 createMic 배포 후 이미 silent.)

### 데이터 흐름
30초 무발화 → `_listen_timeout` 발화 → `web_pub.emit("mic_release")` → publisher→DO→broadcast(owner)
→ 웹 `mic_release` → voiceOn off + 버튼 off + 캡처 해제. jarvis 는 `_set_idle`(WAITING_WAKE). 재개=음성버튼 재탭.

### 테스트 (tests/test_conversation.py 보강)
- hands_free + 작은 `hands_free_timeout_s` 주입 → watchdog 발화 시 `web_pub.emit("mic_release")` + mode IDLE.
- 비-hands_free → mic_release 없이 idle.
- (Part A 로 fake recognizer 에 async resume/suspend/aclose no-op 추가됨 → 기존 컨트롤러 테스트 유지.)

---

## 의도된 동작 변경
- 핸즈프리 청취가 무한이 아니라 30초 무발화 시 종료 + 웹 마이크 해제(이전: 무한 유지).
- 일반 대화 STT 가 설정에 따라 Gladia 가능(기본 local = 현 동작).

## 검증
- `.venv/bin/python -m pytest -q` — 기존 + 신규(conversation_stt, 컨트롤러 타임아웃) 통과.
- `.venv/bin/python -c "import main, conversation, conversation_stt, gladia_stt"` — import ok.
- `cd jarvis-web && npm run typecheck` — 0.
- 수동(배포/재시작 후): 설정에서 일반 STT=Gladia → 음성 대화 Gladia 자막; =로컬 → RealtimeSTT.
  음성 ON → 30초 무발화 → 버튼 자동 off(얼럿 없음)·대기 복귀.

## 영향 파일
| 파일 | 변경 |
|------|------|
| `conversation_stt.py` (신규) | `ConversationSTT` facade |
| `gladia_stt.py` | `feed_block` 추가 |
| `conversation.py` | resume/suspend 연동, `_listen_timeout` 30초+mic_release, `hands_free_timeout_s` |
| `settings.py` | `conversation_stt_backend` |
| `config.py` | `HANDS_FREE_TIMEOUT_S` |
| `main.py` | ConversationSTT 배선, hands_free_timeout_s 주입, aclose |
| `jarvis-web/src/types.ts` | `mic_release` kind |
| `jarvis-web/src/meeting_do.ts` | mic_release broadcast(append X) |
| `jarvis-web/src/static/app.html` | 설정 라디오 행 + handle mic_release |
| `tests/test_conversation_stt.py` (신규), `tests/test_conversation.py` | 테스트 |

배포: A/B 의 jarvis 부분 = 재시작; 웹 부분(설정 행·mic_release·types/DO) = `wrangler deploy`. origin push 직접.
