# RealtimeSTT 어댑터 추출 + intent 리네임 설계

날짜: 2026-06-06

## 목표
- **Part C**: `streaming_stt.py` 와 `live_translate.py(MeetingSession)` 가 각자 재구현한
  RealtimeSTT recorder 래핑(생성·partial threadsafe·listen 루프·feed·shutdown)을
  신규 `realtime_stt.RealtimeSTTAdapter` 로 통합한다.
- **Part D**: `intent.py`(모드의도) / `intents.py`(음악의도)의 단·복수 혼동을 리네임으로 제거한다.

성격: **순수 추출/리네임, 외부 동작 보존**(무음 플러시 포함). C 는 회의·일반 대화 STT 를
건드리나 어댑터는 fake recorder 로 단위테스트 가능하고 회의 주 backend 는 Gladia(RT 는 폴백)라
위험이 봉쇄된다.

## 비범위 (YAGNI)
- Gladia STT(`gladia_stt.py`) 변경 없음.
- 번역(`_consume_finals`/`_translate_bg`) 로직 변경 없음 — RT/Gladia 가 합류하는 `_final_q`
  소비자는 그대로.
- `main.py` 의 recognizer 배선은 그대로(공개 클래스명 `StreamingRecognizer` 유지).
- 재연결/백오프(이미 분리됨), 모드머신 등 무관 영역 미변경.

---

## Part C — RealtimeSTT 어댑터

### 현재 중복
| 관심사 | streaming_stt.py | live_translate.py |
|---|---|---|
| recorder 생성(use_microphone=False, 동일 kwargs) | `_make_recorder` | `start()` 184-203 |
| partial dedup + threadsafe 마샬링 | `_on_partial` 37-46 | `_on_partial` 263-281 |
| listen 루프(`to_thread(recorder.text, cb)`) | `_listen_loop` | `_listen_loop` 283-301 |
| float32→int16 변환 후 feed | `feed_block` 29-35 | `feed_block` 152-161 |
| shutdown | `close` | `stop()` 212-223 |

차이: streaming 은 무음 플러시(`_maybe_flush`/`_flush_loop`) 보유; live_translate 는
initial_prompt(wordbook) 사용 + Gladia 와 backend 택일. recorder kwargs(모델/감도/silence)는 동일.

### 신규 `realtime_stt.py`
```python
def to_pcm16(block) -> bytes:
    """float32[-1,1] → int16 LE PCM bytes (두 곳의 동일 변환 통합)."""
    return (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class RealtimeSTTAdapter:
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="", initial_prompt=None, on_log=print,
                 recorder_factory=None, clock=time.monotonic,
                 silence_flush=False, flush_after=1.2):
        ...
    async def start(self):       # loop 획득, recorder 생성, listen 태스크(+옵션 flush 태스크)
    def feed_pcm16(self, b):     # recorder.feed_audio(b, 16000) + _last_feed_ts 갱신
    def feed_block(self, block): # feed_pcm16(to_pcm16(block))
    async def close(self):       # flush·listen 태스크 취소 + recorder.shutdown()
```
내부:
- recorder 생성: `recorder_factory(partial_cb)` 있으면 그것, 없으면
  `AudioToTextRecorder(model, realtime_model_type, enable_realtime_transcription=True,
  on_realtime_transcription_update=partial_cb, language, [initial_prompt/initial_prompt_realtime
  if initial_prompt], spinner=False, post_speech_silence_duration=0.7, silero_sensitivity=0.4,
  webrtc_sensitivity=3, device="cpu", compute_type="int8", level=30, use_microphone=False)`.
- `partial_cb(text)`(레코더 스레드): dedup(`_partial_last`) 후 `loop.call_soon_threadsafe(on_partial, text)`.
- `_listen_loop`: `await asyncio.to_thread(recorder.text, _final_cb)` 반복; `_final_cb(t)` →
  `loop.call_soon_threadsafe(self._dispatch_final, (t or "").strip())`;
  `_dispatch_final(t)`: `self._partial_last=""; if t: on_final(t)`.
- 무음 플러시(silence_flush=True): `_maybe_flush(now)` — `_partial_last` 있고 마지막 feed 후
  `flush_after` 초 경과면 ~1s 무음 pcm16 주입(현재 streaming 로직 동일). `_flush_loop` 0.2s 폴링.

> on_final 은 동기 콜백으로 호출(루프에서). streaming 은 `controller.on_final`(동기),
> live_translate 은 `_final_q.put_nowait`(동기) — 둘 다 동기라 어댑터 내부 final 큐/소비자 불필요.
> (기존 streaming 의 `_final_q`/`_consume_finals` 의 코루틴 final 처리는 제거 — 현재 호출자 모두 동기.)

### `streaming_stt.py` — 얇은 위임 shim
```python
from realtime_stt import RealtimeSTTAdapter

class StreamingRecognizer(RealtimeSTTAdapter):
    """일반 대화용 — 무음 플러시 켜고 RealtimeSTTAdapter 그대로 사용."""
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="ko", on_log=print, recorder_factory=None):
        super().__init__(on_partial=on_partial, on_final=on_final, model=model,
                         realtime_model=realtime_model, language=language, on_log=on_log,
                         recorder_factory=recorder_factory, silence_flush=True)
```
`main.py` 의 `from streaming_stt import StreamingRecognizer` 및 생성 호출 **무변경**.

### `live_translate.py` — RT 분기 어댑터화
- 필드: `self.recorder`/`self._listen_task` → `self._rt = None`(어댑터).
- `start()` RT 분기: inline recorder/`_listen_task` 대신
  ```python
  from realtime_stt import RealtimeSTTAdapter
  wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH)
  self._rt = RealtimeSTTAdapter(
      on_partial=self._stt_partial, on_final=self._stt_final,
      model=self.model, realtime_model=self.realtime_model, language=self.language,
      initial_prompt=wb_prompt, on_log=self.log)
  await self._rt.start()
  ```
  → RT 도 Gladia 와 **동일 콜백**(`_stt_partial`/`_stt_final`) 사용. `_on_partial`·`_listen_loop` 삭제.
- `feed_block`: `pcm16 = to_pcm16(block)` 후 `self._stt`(Gladia)면 `feed_pcm`, 아니면 `self._rt.feed_pcm16`.
  (가드: `self._stt is None and self._rt is None` 이면 return.)
- `stop()`: `recorder.shutdown()` + `_listen_task` 취소 대신 `if self._rt: await self._rt.close()`.
  Gladia(`self._stt`) 정리·`_final_q` 센티넬·`_consume_finals`·`_relay` 정리는 그대로.
- `_consume_finals`·`_translate_bg`·`_emit`·`_stt_partial`·`_stt_final` 불변.

### 테스트
- 신규 `tests/test_realtime_stt.py`: fake recorder(+`recorder_factory`)로
  - partial dedup + `on_partial` 호출(빈/중복 무시),
  - `_dispatch_final` 이 `_partial_last` 리셋 + `on_final(text)` 호출(빈 텍스트 무시),
  - `feed_block`/`feed_pcm16` 가 int16 변환·`_last_feed_ts` 갱신,
  - `_maybe_flush`(silence_flush): partial 대기 + 공급 정체 시 무음 주입, 아니면 미주입,
  - `recorder_factory` 없이 import 동작(생성 분기).
  (기존 `test_streaming_stt.py` 의 dedup/feed/flush 단위테스트를 여기로 이관.)
- `tests/test_streaming_stt.py`: StreamingRecognizer 가 RealtimeSTTAdapter 서브클래스이고
  `silence_flush=True` 로 동작하는 스모크(생성 + feed_block 위임) 정도만 남김.
- `tests/test_meeting_session.py`: 기존 통과 유지(필요 시 `self._rt` 명칭 반영). import 확인.

---

## Part D — intent / intents 리네임

- `git mv intent.py mode_intent.py` (함수 `mode_intent` 유지).
  - `main.py:32` `from intent import mode_intent` → `from mode_intent import mode_intent`.
  - `tests/test_intent.py:2` `from intent import mode_intent` → `from mode_intent import mode_intent`.
    (파일명은 `test_intent.py` 유지 가능 — 또는 선택적으로 `git mv` 로 `test_mode_intent.py`. 본 설계는 내용만 갱신.)
- `git mv intents.py music_intent.py` (함수 `classify` 유지).
  - `llm.py:21` `import intents` → `import music_intent`; `llm.py:231` `intents.classify(...)` → `music_intent.classify(...)`.
  - 음악 의도 전용 테스트는 없음(변경 불필요).
- 순수 리네임 — 로직/동작 불변.

---

## 데이터 흐름 / 동작
변경 없음. C: RT 경로가 어댑터를 거쳐 동일 콜백으로 `_final_q`(meeting)/`controller.on_final`
(conversation)로 합류. D: 모듈명만 변경.

## 검증
- `.venv/bin/python -m pytest -q` — 기존 + 신규 전부 통과.
- `.venv/bin/python -c "import main, live_translate, streaming_stt, realtime_stt, mode_intent, music_intent, llm"` — import ok.
- 수동(권장, 배포/재시작 후): 일반 음성 대화(streaming STT) 발화→응답, 회의 모드 Gladia
  자막 + (Gladia 미설정 시) RT 폴백 자막.

## 영향 파일
| 파일 | 변경 |
|------|------|
| `realtime_stt.py` (신규) | `RealtimeSTTAdapter` + `to_pcm16` |
| `streaming_stt.py` | `StreamingRecognizer` = 어댑터 서브클래스(shim) |
| `live_translate.py` | RT 분기 어댑터화, `_on_partial`/`_listen_loop` 제거, feed/stop 갱신 |
| `tests/test_realtime_stt.py` (신규) | 어댑터 단위테스트(streaming 단위테스트 이관) |
| `tests/test_streaming_stt.py` | 위임 스모크로 축소 |
| `intent.py`→`mode_intent.py`, `intents.py`→`music_intent.py` | git mv |
| `main.py`, `llm.py`, `tests/test_intent.py` | import 갱신 |

배포: jarvis 재시작(웹/서버 변경 없음). origin push 는 사용자가 직접.
