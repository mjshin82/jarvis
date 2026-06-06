# RealtimeSTT 어댑터 + intent 리네임 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RealtimeSTT recorder 래핑 중복을 `realtime_stt.RealtimeSTTAdapter` 로 통합(C)하고, `intent.py`/`intents.py` 의 단·복수 혼동을 리네임(D)한다.

**Architecture:** 어댑터가 recorder 생성·partial dedup/threadsafe·listen 루프·feed·shutdown·(옵션)무음플러시를 소유. `StreamingRecognizer` 는 어댑터의 얇은 서브클래스(silence_flush=True), `MeetingSession` 의 RT 폴백 분기는 어댑터를 쓰고 두 backend(Gladia/RT)가 동일 콜백으로 합류. D 는 git mv + import 갱신.

**Tech Stack:** Python 3.11 asyncio + pytest(`.venv/bin/python -m pytest`). 순수 추출/리네임, 외부 동작 보존(무음 플러시 포함).

**스펙:** `docs/superpowers/specs/2026-06-06-realtime-stt-adapter-and-intent-rename-design.md`

---

## Part C — RealtimeSTT 어댑터

### Task 1: `realtime_stt.py` 어댑터 + 단위테스트 (TDD)

**Files:**
- Create: `realtime_stt.py`
- Test: `tests/test_realtime_stt.py`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_realtime_stt.py`

```python
import asyncio
import numpy as np
from realtime_stt import RealtimeSTTAdapter, to_pcm16


class _FakeRecorder:
    def __init__(self): self.fed = []
    def feed_audio(self, pcm, sr): self.fed.append((pcm, sr))


def _adapter(**over):
    deps = dict(on_partial=lambda t: None, on_final=lambda t: None)
    deps.update(over)
    return RealtimeSTTAdapter(**deps)


def test_to_pcm16():
    pcm = np.frombuffer(to_pcm16(np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)), dtype="<i2")
    assert pcm[0] == 0 and pcm[1] == 32767 and pcm[2] == -32767


def test_partial_dedup_and_strip():
    seen = []
    a = _adapter(on_partial=lambda t: seen.append(t))
    a._on_partial("안녕"); a._on_partial("안녕"); a._on_partial("  안녕하세요 "); a._on_partial("")
    assert seen == ["안녕", "안녕하세요"]


def test_feed_block_converts_and_updates_ts():
    a = _adapter(clock=lambda: 123.0)
    a.recorder = _FakeRecorder()
    a.feed_block(np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32))
    assert len(a.recorder.fed) == 1
    pcm, sr = a.recorder.fed[0]
    assert sr == 16000
    assert np.frombuffer(pcm, dtype="<i2")[1] == 32767
    assert a._last_feed_ts == 123.0


def test_feed_block_noop_without_recorder():
    _adapter().feed_block(np.zeros(4, dtype=np.float32))   # recorder None → 무시(예외 없음)


def test_dispatch_final_resets_partial_and_calls_on_final():
    got = []
    a = _adapter(on_final=lambda t: got.append(t))
    a._partial_last = "진행중"
    a._dispatch_final("최종")
    assert got == ["최종"] and a._partial_last == ""
    a._partial_last = "x"
    a._dispatch_final("")              # 빈 텍스트 → on_final 미호출, partial 리셋
    assert got == ["최종"] and a._partial_last == ""


def test_maybe_flush_injects_silence_when_stalled():
    a = _adapter(flush_after=1.2)
    a.recorder = _FakeRecorder()
    a._partial_last = "오늘은 며칠이야"
    a._last_feed_ts = 100.0
    assert a._maybe_flush(100.0 + 1.1) is False
    assert a.recorder.fed == []
    assert a._maybe_flush(100.0 + 1.3) is True
    pcm, sr = a.recorder.fed[0]
    assert sr == 16000
    arr = np.frombuffer(pcm, dtype="<i2")
    assert arr.size > 0 and not arr.any()
    assert a._maybe_flush(100.0 + 1.4) is False   # 방금 주입 → gap 리셋, 재주입 안 함


def test_maybe_flush_noop_without_pending_partial():
    a = _adapter()
    a.recorder = _FakeRecorder()
    a._partial_last = ""
    assert a._maybe_flush(10_000.0) is False
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_realtime_stt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'realtime_stt'`

- [ ] **Step 3: 구현** — `realtime_stt.py`

```python
# realtime_stt.py
"""RealtimeSTT recorder 공용 래퍼.

streaming_stt(일반 대화)와 live_translate(회의 RT 폴백)가 공유:
recorder 생성·partial dedup/threadsafe 마샬링·listen 루프·feed·shutdown,
그리고 (옵션) 공급 정체 시 무음 주입 플러시.
"""
import asyncio
import time

import numpy as np

_FLUSH_SILENCE_S = 1.0   # 강제 final 유도용 무음 길이(초)


def to_pcm16(block) -> bytes:
    """float32[-1,1] → int16 LE PCM bytes."""
    return (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class RealtimeSTTAdapter:
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="", initial_prompt=None, on_log=print,
                 recorder_factory=None, clock=time.monotonic,
                 silence_flush=False, flush_after=1.2):
        self.on_partial = on_partial
        self.on_final = on_final
        self.model = model
        self.realtime_model = realtime_model
        self.language = language
        self.initial_prompt = initial_prompt
        self.log = on_log
        self._recorder_factory = recorder_factory
        self._clock = clock
        self._silence_flush = silence_flush
        self._flush_after = flush_after
        self.recorder = None
        self._loop = None
        self._listen_task = None
        self._flush_task = None
        self._partial_last = ""
        self._last_feed_ts = 0.0

    # --- feed ---
    def feed_pcm16(self, pcm16: bytes) -> None:
        if self.recorder is None:
            return
        self.recorder.feed_audio(pcm16, 16000)
        self._last_feed_ts = self._clock()

    def feed_block(self, block) -> None:
        self.feed_pcm16(to_pcm16(block))

    # --- partial (레코더 스레드에서 호출 → 메인 루프로 마샬링) ---
    def _on_partial(self, text):
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_partial, text)
        else:
            self.on_partial(text)

    # --- final (메인 루프에서) ---
    def _dispatch_final(self, text):
        self._partial_last = ""
        if text:
            self.on_final(text)

    # --- 무음 플러시 ---
    def _maybe_flush(self, now) -> bool:
        if self.recorder is None or not self._partial_last:
            return False
        if now - self._last_feed_ts < self._flush_after:
            return False
        silence = np.zeros(int(16000 * _FLUSH_SILENCE_S), dtype=np.int16).tobytes()
        try:
            self.recorder.feed_audio(silence, 16000)
        except Exception:
            return False
        self._last_feed_ts = now
        return True

    async def _flush_loop(self):
        try:
            while True:
                await asyncio.sleep(0.2)
                self._maybe_flush(self._clock())
        except asyncio.CancelledError:
            return

    # --- 레코더 생성 ---
    def _make_recorder(self):
        if self._recorder_factory is not None:
            return self._recorder_factory(self._on_partial)
        from RealtimeSTT import AudioToTextRecorder
        kwargs = dict(
            model=self.model,
            realtime_model_type=self.realtime_model,
            enable_realtime_transcription=True,
            on_realtime_transcription_update=self._on_partial,
            language=self.language,
            spinner=False,
            post_speech_silence_duration=0.7,
            silero_sensitivity=0.4,
            webrtc_sensitivity=3,
            device="cpu",
            compute_type="int8",
            level=30,
            use_microphone=False,
        )
        if self.initial_prompt:
            kwargs["initial_prompt"] = self.initial_prompt
            kwargs["initial_prompt_realtime"] = self.initial_prompt
        return AudioToTextRecorder(**kwargs)

    async def _listen_loop(self):
        try:
            while True:
                def _final_cb(t):
                    try:
                        self._loop.call_soon_threadsafe(self._dispatch_final, (t or "").strip())
                    except Exception:
                        pass
                await asyncio.to_thread(self.recorder.text, _final_cb)
        except asyncio.CancelledError:
            return
        except Exception as ex:
            try:
                self.log(f"[stt] listen loop error: {ex}")
            except Exception:
                pass

    # --- 라이프사이클 ---
    async def start(self):
        self._loop = asyncio.get_running_loop()
        self.recorder = self._make_recorder()
        self._last_feed_ts = self._clock()
        self._listen_task = asyncio.create_task(self._listen_loop())
        if self._silence_flush:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def close(self):
        for task in (self._flush_task, self._listen_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._flush_task = None
        self._listen_task = None
        if self.recorder is not None:
            try:
                self.recorder.shutdown()
            except Exception:
                pass
            self.recorder = None
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_realtime_stt.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: 커밋**

```bash
git add realtime_stt.py tests/test_realtime_stt.py
git commit -m "feat(realtime-stt): RealtimeSTTAdapter + to_pcm16 + 단위테스트"
```

---

### Task 2: `streaming_stt.py` → 어댑터 서브클래스 shim + 테스트 축소

**Files:**
- Modify: `streaming_stt.py` (전체 교체)
- Modify: `tests/test_streaming_stt.py` (스모크로 축소)

- [ ] **Step 1: `streaming_stt.py` 전체를 다음으로 교체**

```python
# streaming_stt.py
"""일반 대화용 스트리밍 STT — RealtimeSTTAdapter 에 무음 플러시를 켠 얇은 래퍼.

main.py 가 import 하는 공개 이름. recorder 래핑/플러시 로직은 realtime_stt 에 있다.
"""
from realtime_stt import RealtimeSTTAdapter


class StreamingRecognizer(RealtimeSTTAdapter):
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="ko", on_log=print, recorder_factory=None):
        super().__init__(
            on_partial=on_partial, on_final=on_final, model=model,
            realtime_model=realtime_model, language=language, on_log=on_log,
            recorder_factory=recorder_factory, silence_flush=True,
        )
```

- [ ] **Step 2: `tests/test_streaming_stt.py` 전체를 다음으로 교체** (상세 단위테스트는 Task 1 의 어댑터 테스트로 이관됨)

```python
import numpy as np
from realtime_stt import RealtimeSTTAdapter
from streaming_stt import StreamingRecognizer


class _FakeRecorder:
    def __init__(self): self.fed = []
    def feed_audio(self, pcm, sr): self.fed.append((pcm, sr))


def test_is_adapter_subclass_with_silence_flush():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    assert isinstance(rx, RealtimeSTTAdapter)
    assert rx._silence_flush is True
    assert rx.language == "ko"


def test_feed_block_delegates_to_adapter():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.recorder = _FakeRecorder()
    rx.feed_block(np.array([0.0, 1.0], dtype=np.float32))
    assert len(rx.recorder.fed) == 1
```

- [ ] **Step 3: import + 전체 테스트**

Run:
```bash
.venv/bin/python -c "import main, streaming_stt, realtime_stt; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, all passed (≈101: 기존 99 − streaming 6 + realtime 8). 실패가 없어야 한다.

- [ ] **Step 4: 커밋**

```bash
git add streaming_stt.py tests/test_streaming_stt.py
git commit -m "refactor(streaming-stt): RealtimeSTTAdapter 서브클래스로 축소"
```

---

### Task 3: `live_translate.py` RT 분기 어댑터화

**Files:**
- Modify: `live_translate.py`
- Modify: `tests/test_meeting_session.py`

- [ ] **Step 1: import 추가**

`live_translate.py` 상단 import 블록(다른 `import` 근처)에 추가:
```python
from realtime_stt import RealtimeSTTAdapter, to_pcm16
```

- [ ] **Step 2: 필드 교체** — `__init__` 의
```python
        self.recorder = None
        self._stt = None        # 스트리밍 STT 백엔드(Gladia)
```
는 `self._stt = None` 유지하고 `self.recorder = None` 을 `self._rt = None        # RealtimeSTT 폴백(어댑터)` 로 바꾼다. 그리고 `self._listen_task = None` 줄을 삭제한다.

- [ ] **Step 3: `feed_block` 교체** (현재 152-161)

```python
    def feed_block(self, block) -> None:
        """MicRouter tap 이 매 블록 호출 — float32 → int16 PCM 으로 활성 STT 백엔드 주입."""
        if self._stt is None and self._rt is None:
            return
        pcm16 = to_pcm16(block)
        if self._stt is not None:
            self._stt.feed_pcm(pcm16)
        else:
            self._rt.feed_pcm16(pcm16)
```

- [ ] **Step 4: `start()` 의 RT 분기 교체** (현재 183-204의 `if self._stt is None:` 블록)

```python
        if self._stt is None:
            wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH)
            self._rt = RealtimeSTTAdapter(
                on_partial=self._stt_partial, on_final=self._stt_final,
                model=self.model, realtime_model=self.realtime_model, language=self.language,
                initial_prompt=wb_prompt, on_log=self.log,
            )
            await self._rt.start()
```
(그 아래 `self._setup_translator()` / `self._consumer_task = asyncio.create_task(self._consume_finals())` / 로그는 그대로.)

- [ ] **Step 5: `stop()` 의 recorder/listen 정리 교체** (현재 212-223)

현재:
```python
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.recorder is not None:
            try:
                self.recorder.shutdown()
            except Exception:
                pass
            self.recorder = None
```
교체 후:
```python
        if self._rt is not None:
            try:
                await self._rt.close()
            except Exception:
                pass
            self._rt = None
```
(그 아래 Gladia `self._stt` close, `_final_q` 센티넬, `_consume_finals` 대기, `_relay` 정리는 그대로.)

- [ ] **Step 6: `_on_partial`, `_listen_loop` 메서드 삭제** (현재 263-281, 283-301 — 어댑터로 이전됨)

`_stt_partial`, `_stt_final`, `_emit`, `_consume_finals`, `_translate_bg` 는 유지.

- [ ] **Step 7: `tests/test_meeting_session.py` 의 feed_block 테스트 갱신**

현재 `test_feed_block_converts_float32_to_int16_bytes` 와 `test_feed_block_noop_without_recorder` 를 다음으로 교체(나머지 `test_no_use_remote_param` 유지):
```python
def test_feed_block_converts_float32_to_int16_bytes():
    sess = _sess()
    calls = []

    class FakeRT:
        def feed_pcm16(self, chunk): calls.append(chunk)

    sess._rt = FakeRT()
    sess.feed_block(np.array([0.5, -0.5, 0.0, 1.0], dtype=np.float32))
    assert len(calls) == 1
    arr = np.frombuffer(calls[0], dtype="<i2")
    assert arr[0] == 16383      # 0.5 * 32767 → 16383 (절삭)
    assert arr[1] == -16383
    assert arr[2] == 0
    assert arr[3] == 32767      # 1.0 클립


def test_feed_block_noop_without_recorder():
    sess = _sess()
    sess._stt = None
    sess._rt = None
    sess.feed_block(np.zeros(4, dtype=np.float32))   # 예외 없이 무시
```

- [ ] **Step 8: import + 전체 테스트**

Run:
```bash
.venv/bin/python -c "import main, live_translate; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, all passed(실패 0).

- [ ] **Step 9: 커밋**

```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "refactor(meeting): RT 폴백을 RealtimeSTTAdapter 로, 두 backend 콜백 합류"
```

---

## Part D — intent / intents 리네임

### Task 4: 모듈 리네임 + import 갱신

**Files:**
- Rename: `intent.py` → `mode_intent.py`, `intents.py` → `music_intent.py`
- Modify: `main.py`, `llm.py`, `tests/test_intent.py`

- [ ] **Step 1: git mv (이력 보존)**

```bash
cd /Users/oracle/Documents/concode/jarvis
git mv intent.py mode_intent.py
git mv intents.py music_intent.py
```

- [ ] **Step 2: import 갱신**

- `main.py`: `from intent import mode_intent` → `from mode_intent import mode_intent`.
- `llm.py`: `import intents` → `import music_intent`; 그리고 `intents.classify(` 호출을 `music_intent.classify(` 로 (현재 1곳, llm.py:231 부근).
- `tests/test_intent.py`: `from intent import mode_intent` → `from mode_intent import mode_intent`.

각 파일에서 다른 `intent`/`intents` 잔존 참조가 없는지 grep 으로 확인:
```bash
grep -rn "import intent\b\|from intent import\|import intents\b\|from intents import\|intents\." *.py tests/*.py
```
기대: 매치 없음(전부 mode_intent / music_intent 로 치환됨).

- [ ] **Step 3: import + 전체 테스트**

Run:
```bash
.venv/bin/python -c "import main, llm, mode_intent, music_intent; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, all passed(실패 0).

- [ ] **Step 4: 커밋**

```bash
git add -A
git commit -m "refactor(intent): intent.py→mode_intent.py, intents.py→music_intent.py 리네임"
```

---

## Task 5: 최종 검증

**Files:** (변경 없음)

- [ ] **Step 1: 전체 import + 테스트**

Run:
```bash
.venv/bin/python -c "import main, live_translate, streaming_stt, realtime_stt, mode_intent, music_intent, llm; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과(실패 0).

- [ ] **Step 2: 잔존 옛 이름 확인**

Run:
```bash
grep -rn "from intent import\|import intents\b\|intents\.\|class StreamingRecognizer" *.py | grep -v "music_intent\|mode_intent"
ls intent.py intents.py 2>/dev/null || echo "옛 파일 없음(정상)"
```
기대: `옛 파일 없음(정상)`, StreamingRecognizer 는 streaming_stt.py 정의만.

- [ ] **Step 3: 수동(권장, 재시작 후)**
- 일반 음성 대화: 'Hey Jarvis' → 발화 → 응답(streaming STT 정상).
- 회의: Gladia 자막(주 경로) / Gladia 미설정 시 RealtimeSTT 폴백 자막.
- 음성 모드전환 의도("회의 시작/종료")·음악 의도("음악 틀어줘/꺼줘") 정상.

---

## 비고
- 순수 추출/리네임 — 외부 동작 보존(무음 플러시 포함). main.py 무변경(StreamingRecognizer 공개명 유지).
- 배포: jarvis 재시작(웹/서버 변경 없음). origin push 는 사용자가 직접.
- `git mv` 로 파일 이력 보존.
