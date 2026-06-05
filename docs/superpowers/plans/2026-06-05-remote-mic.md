# 원격 마이크 (Remote Mic) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 마이크 입력 소스를 추상화해, 기본 시스템 마이크에 더해 웹 프론트(meeting-web)를 통한 외부 기기(폰/타블렛) 마이크 스트림을 입력으로 받게 한다.

**Architecture:** `Microphone`의 512-샘플 블록 공급원을 `MicRouter`(`LocalMicSource` ↔ `RemoteMicSource`)로 분리한다. VAD/wake/utterance 로직은 불변. 원격 오디오는 jarvis에 인바운드 서버를 두지 않고 기존 meeting-web Cloudflare relay를 역방향(`/mic` 송신 → `/mic-recv` 수신)으로 재사용한다.

**Tech Stack:** Python 3.11 (asyncio, sounddevice, numpy, websockets, silero-vad, pytest) · Cloudflare Workers (Hono + Durable Objects, TypeScript).

---

## 파일 구조

| 파일 | 책임 | 상태 |
|------|------|------|
| `mic_source.py` | `LocalMicSource`(sounddevice), `RemoteMicSource`(Int16→float32→512 재청크), `MicRouter`(소스 선택/자동전환/오버라이드) | 신규 |
| `remote_mic_receiver.py` | relay에 인증 WS 영속 연결, binary mic 프레임 수신 → router 주입, 백오프 재연결 | 신규 |
| `audio_io.py` | `Microphone`: sounddevice 직결 제거 → `MicRouter` 큐 소비. VAD/events 불변. VAD 주입 seam 추가 | 수정 |
| `config.py` | `REMOTE_MIC_ENABLED`, `REMOTE_MIC_KEY`, `REMOTE_MIC_IDLE_S` (RELAY_URL/TOKEN 재사용) | 수정 |
| `commands.py` | `/mic` 오버로드: `phone\|system\|auto` 인자로 소스 전환, 무인자는 기존 듣기 진입 | 수정 |
| `main.py` | router/receiver 배선, 캡처 URL 박스, `/mic` ctx 주입 | 수정 |
| `meeting-web/src/types.ts` | mic 제어 메시지 kind 추가 | 수정 |
| `meeting-web/src/index.ts` | `/mic/:key`, `/mic-recv/:key`(Bearer), `/capture/:key`(HTML) 라우트 | 수정 |
| `meeting-web/src/meeting_do.ts` | micSender↔micReceiver 슬롯 + binary 포워딩 | 수정 |
| `meeting-web/src/static/capture.html` | 캡처 페이지(getUserMedia→16k Int16→ws binary) | 신규 |

테스트: `tests/test_remote_mic_source.py`, `tests/test_mic_router.py`, `tests/test_local_mic_source.py`, `tests/test_microphone_events.py`, `tests/test_remote_mic_receiver.py`, `tests/test_mic_command.py`, `meeting-web/scripts/mic_relay_check.mjs`.

전제: 항상 `cd /Users/oracle/Documents/concode/jarvis`. pytest는 `.venv` 활성 또는 `python -m pytest`로 실행.

---

## Task 1: RemoteMicSource — Int16 PCM → float32 → 512 재청크

원격 프레임(16kHz mono Int16)을 받아 float32 [-1,1)로 변환하고 정확히 `BLOCK_SIZE`(512) 샘플 블록으로 재청크해 sink 콜백으로 방출한다. 동기 함수(수신 asyncio 스레드에서 호출).

**Files:**
- Create: `mic_source.py`
- Test: `tests/test_remote_mic_source.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_remote_mic_source.py
import numpy as np
import config
from mic_source import RemoteMicSource


def test_feed_rechunks_to_block_size_and_scales():
    out = []
    src = RemoteMicSource(sink=out.append)
    # int16 341개(512 미만) → 아직 방출 없음
    src.feed(np.zeros(341, dtype=np.int16).tobytes())
    assert out == []
    # 추가 1480개 → 합 1821 = 512*3 + 285 → 3블록 방출, 285 잔여
    src.feed((np.ones(1480, dtype=np.int16) * 16384).tobytes())
    assert len(out) == 3
    assert all(b.shape == (config.BLOCK_SIZE,) for b in out)
    assert all(b.dtype == np.float32 for b in out)
    # 16384/32768 = 0.5 로 스케일된 값이 두 번째 이후 블록에 존재
    assert np.isclose(out[-1][-1], 0.5, atol=1e-3)


def test_reset_clears_partial_buffer():
    out = []
    src = RemoteMicSource(sink=out.append)
    src.feed(np.ones(300, dtype=np.int16).tobytes())   # 잔여 300
    src.reset()
    src.feed(np.ones(512, dtype=np.int16).tobytes())   # reset 후 정확히 1블록
    assert len(out) == 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_remote_mic_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mic_source'`

- [ ] **Step 3: 최소 구현**

```python
# mic_source.py
"""마이크 입력 소스 추상화.

Microphone 이 소비하는 512-샘플 float32 블록의 공급원을 분리한다:
  - LocalMicSource  : sounddevice InputStream (기본)
  - RemoteMicSource : 외부에서 주입된 Int16 PCM → float32 → 512 재청크
  - MicRouter       : 활성 소스 선택(자동 전환 + 수동 오버라이드)

모든 소스는 동일한 sink(block: np.ndarray)->None 으로 블록을 흘려보낸다.
"""
import queue
import time

import numpy as np

import config


class RemoteMicSource:
    """주입된 16kHz mono Int16 PCM 을 float32 512-블록으로 재청크해 sink 로 방출."""

    def __init__(self, sink):
        self._sink = sink
        self._buf = np.empty(0, dtype=np.float32)

    def feed(self, pcm_bytes: bytes) -> None:
        """Int16 little-endian PCM 바이트를 받아 누적·재청크."""
        samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, samples])
        bs = config.BLOCK_SIZE
        while len(self._buf) >= bs:
            self._sink(np.ascontiguousarray(self._buf[:bs]))
            self._buf = self._buf[bs:]

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.float32)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_remote_mic_source.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add mic_source.py tests/test_remote_mic_source.py
git commit -m "feat: RemoteMicSource — Int16 PCM 을 512 float32 블록으로 재청크"
```

---

## Task 2: MicRouter — 소스 선택, 자동 전환, 수동 오버라이드

활성 소스의 블록만 큐로 흘린다. `auto` 모드에서 원격 활동 감지 시 remote 로, idle 초과 시 local 로 복귀. `/mic` 명령은 `local`/`remote`/`auto` 오버라이드.

**Files:**
- Modify: `mic_source.py`
- Test: `tests/test_mic_router.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_mic_router.py
import queue
import numpy as np
import mic_source
from mic_source import MicRouter


def _block(v=0.0):
    return np.full(512, v, dtype=np.float32)


def test_only_active_source_reaches_queue():
    q = queue.Queue()
    r = MicRouter(q, local=object(), remote=object())   # 소스는 안 씀(게이팅만 검증)
    # 기본 active=local
    r._sink_remote(_block(0.1))
    assert q.empty()
    r._sink_local(_block(0.2))
    assert q.qsize() == 1


def test_auto_switches_to_remote_on_activity_and_back_on_idle():
    q = queue.Queue()
    r = MicRouter(q, local=object(), remote=_FakeRemote())
    r.note_remote_activity(now=100.0)
    assert r._active == "remote"
    r._sink_remote(_block()); assert q.qsize() == 1
    # idle 미만 → 유지
    r.check_idle(now=100.0 + config.REMOTE_MIC_IDLE_S - 0.1)
    assert r._active == "remote"
    # idle 초과 → local 복귀 + 큐 비움
    r.check_idle(now=100.0 + config.REMOTE_MIC_IDLE_S + 0.1)
    assert r._active == "local"
    assert q.empty()


def test_manual_override_beats_auto():
    q = queue.Queue()
    r = MicRouter(q, local=object(), remote=_FakeRemote())
    r.set_override("remote")
    r.note_remote_activity(now=0.0)
    # auto 가 아니므로 idle 검사로 안 돌아감
    r.check_idle(now=10_000.0)
    assert r._active == "remote"
    r.set_override("local")
    r.note_remote_activity(now=10_001.0)   # 무시됨
    assert r._active == "local"


class _FakeRemote:
    def reset(self): pass


import config   # noqa: E402  (테스트 말미 import — 위 함수에서 사용)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_mic_router.py -v`
Expected: FAIL — `ImportError: cannot import name 'MicRouter'`

- [ ] **Step 3: 최소 구현 (mic_source.py 에 추가)**

```python
class MicRouter:
    """활성 마이크 소스를 골라 그 블록만 block_queue 로 흘린다.

    mode: 'auto'  — 원격 프레임이 오면 remote, idle 초과 시 local 복귀
          'local' — 시스템 마이크 강제
          'remote'— 원격 강제
    """

    def __init__(self, block_queue, *, local=None, remote=None, clock=time.monotonic):
        self._q = block_queue
        self._clock = clock
        self._mode = "auto"
        self._active = "local"
        self._last_remote = 0.0
        self.local = local if local is not None else LocalMicSource(sink=self._sink_local)
        self.remote = remote if remote is not None else RemoteMicSource(sink=self._sink_remote)

    # --- sink (소스가 블록을 흘려보낼 때 호출) ---
    def _sink_local(self, block):
        if self._active == "local":
            self._q.put(block)

    def _sink_remote(self, block):
        if self._active == "remote":
            self._q.put(block)

    # --- 라이프사이클 ---
    def start(self):
        self.local.start()

    def stop(self):
        self.local.stop()

    def pause_local(self):
        self.local.stop()

    def resume_local(self):
        self.local.start()

    # --- 원격 수신 진입점 (RemoteMicReceiver 가 호출) ---
    def on_remote_frame(self, pcm_bytes):
        self.note_remote_activity(self._clock())
        self.remote.feed(pcm_bytes)

    # --- 전환 로직 ---
    def note_remote_activity(self, now):
        self._last_remote = now
        if self._mode == "auto" and self._active != "remote":
            self._switch("remote")

    def check_idle(self, now):
        if self._mode == "auto" and self._active == "remote":
            if now - self._last_remote > config.REMOTE_MIC_IDLE_S:
                self._switch("local")

    def set_override(self, mode):
        self._mode = mode
        if mode == "local":
            self._switch("local")
        elif mode == "remote":
            self._switch("remote")
        # 'auto': 다음 활동/idle 검사를 따른다

    def _switch(self, target):
        if self._active == target:
            return
        self._active = target
        # 소스 간 오디오 혼입 방지: 큐 잔여 비우고 원격 재청크 버퍼 리셋
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self.remote.reset()

    async def run_idle_monitor(self):
        import asyncio
        while True:
            await asyncio.sleep(0.5)
            self.check_idle(self._clock())
```

> 참고: `LocalMicSource` 는 Task 3 에서 정의한다. Task 2 테스트는 `local=object()` 로 주입하므로 이 시점에 `LocalMicSource` 미정의여도 테스트는 통과한다(기본 인자 분기를 타지 않음).

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_mic_router.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add mic_source.py tests/test_mic_router.py
git commit -m "feat: MicRouter — 활성 소스 게이팅 + 자동전환/오버라이드"
```

---

## Task 3: LocalMicSource — sounddevice 로직 이동

기존 `Microphone._resolve_device`/`_callback`/스트림 개폐 로직을 `LocalMicSource` 로 옮긴다. 512 블록을 sink 로 방출.

**Files:**
- Modify: `mic_source.py`
- Test: `tests/test_local_mic_source.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_local_mic_source.py
import numpy as np
from mic_source import LocalMicSource


def test_callback_forwards_mono_copy_to_sink():
    out = []
    src = LocalMicSource(sink=out.append)
    # sounddevice 콜백 모사: (frames, channels) float32, 1채널만 추출되어야
    indata = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)
    src._callback(indata, 3, None, None)
    assert len(out) == 1
    assert np.array_equal(out[0], np.array([0.1, 0.2, 0.3], dtype=np.float32))
    # 복사본이어야 한다 (원본 변경이 sink 결과에 영향 없음)
    indata[0, 0] = 9.0
    assert out[0][0] == np.float32(0.1)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_local_mic_source.py -v`
Expected: FAIL — `ImportError: cannot import name 'LocalMicSource'`

- [ ] **Step 3: 최소 구현 (mic_source.py 에 추가; 상단에 `import sounddevice as sd` 추가)**

```python
# mic_source.py 상단 import 에 추가
import sounddevice as sd
```

```python
class LocalMicSource:
    """시스템 마이크(sounddevice InputStream) → 512 float32 블록을 sink 로 방출."""

    def __init__(self, sink):
        self._sink = sink
        self._stream = None

    def _resolve_device(self):
        """MIC_DEVICE 우선. 비었으면 입력 채널 있는 첫 물리 마이크 자동 선택
        (BlackHole 등 가상장치 회피)."""
        spec = config.MIC_DEVICE.strip()
        if spec:
            if spec.isdigit():
                return int(spec)
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and spec.lower() in d["name"].lower():
                    return i
            print(f"[audio] MIC_DEVICE='{spec}' 매칭 실패 → 기본 장치 사용")
            return None
        skip = ("blackhole", "loopback", "aggregate", "teams", "soundflower")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0:
                continue
            if any(s in d["name"].lower() for s in skip):
                continue
            return i
        return None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}")
        self._sink(indata[:, 0].copy())

    def start(self):
        if self._stream is not None:
            return
        device = self._resolve_device()
        if device is not None:
            info = sd.query_devices(device)
            print(f"[audio] 입력 장치: [{device}] {info['name']}")
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
            blocksize=config.BLOCK_SIZE,
            dtype="float32",
            callback=self._callback,
            device=device,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_local_mic_source.py tests/test_mic_router.py tests/test_remote_mic_source.py -v`
Expected: PASS (전부)

- [ ] **Step 5: 커밋**

```bash
git add mic_source.py tests/test_local_mic_source.py
git commit -m "feat: LocalMicSource — sounddevice 캡처 로직 분리"
```

---

## Task 4: Microphone 리팩터 — MicRouter 큐 소비 + VAD 주입 seam

`Microphone` 이 sounddevice 를 직접 열지 않고 `self.router`(MicRouter)가 채우는 `self._blocks` 큐를 소비하도록 바꾼다. events()/VAD/wake/utterance 로직은 동일. 테스트를 위해 VAD 이터레이터를 주입 가능하게 한다.

**Files:**
- Modify: `audio_io.py`
- Test: `tests/test_microphone_events.py`

- [ ] **Step 1: 실패 테스트 작성**

가짜 VAD 로 발화 시작/끝을 결정적으로 발생시켜, 원격으로 주입된 프레임이 utterance 로 묶여 나오는지 검증한다(E2E, 실제 silero 로드 없음).

```python
# tests/test_microphone_events.py
import asyncio
import numpy as np
import config
from audio_io import Microphone


class FakeVAD:
    """N번째 호출에 start, M번째에 end 이벤트를 낸다."""
    def __init__(self, start_at, end_at):
        self.n = 0
        self.start_at = start_at
        self.end_at = end_at
    def __call__(self, block):
        self.n += 1
        if self.n == self.start_at:
            return {"start": self.n}
        if self.n == self.end_at:
            return {"end": self.n}
        return None
    def reset_states(self):
        pass


def test_remote_frames_become_utterance_via_events():
    vad = FakeVAD(start_at=2, end_at=4)
    mic = Microphone(vad_default=vad, vad_translate=vad)
    mic.router.set_override("remote")   # 원격 강제 (LocalMicSource 안 엶)

    async def main():
        events = []
        async def consume():
            async for kind, audio in mic.events(wake_detect=None, is_speaking=lambda: False):
                events.append((kind, audio))
                if kind == "utterance":
                    return
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        # 512 샘플(=Int16 1024바이트) 프레임 5개 주입
        frame = (np.ones(512, dtype=np.int16) * 1000).tobytes()
        for _ in range(5):
            mic.router.on_remote_frame(frame)
        await asyncio.wait_for(task, timeout=3)
        kinds = [k for k, _ in events]
        assert "start" in kinds and "utterance" in kinds
        utt = next(a for k, a in events if k == "utterance")
        assert utt.dtype == np.float32 and utt.ndim == 1

    asyncio.run(main())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_microphone_events.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'vad_default'`

- [ ] **Step 3: audio_io.py 리팩터**

`Microphone.__init__` 를 교체(라인 22-41):

```python
    def __init__(self, *, vad_default=None, vad_translate=None):
        self._paused = False
        self._blocks: queue.Queue = queue.Queue()
        from mic_source import MicRouter
        self.router = MicRouter(self._blocks)
        # VAD 주입(테스트) 또는 기본 생성. 모드별 침묵 임계가 다르다.
        if vad_default is None or vad_translate is None:
            self._vad_model = load_silero_vad()
        if vad_default is None:
            vad_default = VADIterator(
                self._vad_model, threshold=config.VAD_THRESHOLD,
                sampling_rate=config.SAMPLE_RATE,
                min_silence_duration_ms=config.SILENCE_MS,
            )
        if vad_translate is None:
            vad_translate = VADIterator(
                self._vad_model, threshold=config.VAD_THRESHOLD,
                sampling_rate=config.SAMPLE_RATE,
                min_silence_duration_ms=config.SILENCE_MS_TRANSLATE,
            )
        self._vad_default = vad_default
        self._vad_translate = vad_translate
        self._vad = self._vad_default
```

`_pick_vad` 는 그대로 둔다. `_resolve_device`(48-68)와 `_callback`(70-74)을 **삭제**한다(LocalMicSource 로 이동됨).

`pause`/`resume`(76-103)를 교체:

```python
    def pause(self) -> None:
        """시스템 마이크 stream 을 닫는다 → 다른 라이브러리가 장치 점유 가능."""
        self._paused = True
        self.router.pause_local()

    def resume(self) -> None:
        """시스템 마이크 stream 을 다시 연다."""
        self.router.resume_local()
        self._paused = False
```

`events()` 의 스트림 개시부(118-131)를 교체:

```python
        loop = asyncio.get_running_loop()
        self.router.start()
```

그리고 `finally` 블록(207-214)을 교체:

```python
        finally:
            self.router.stop()
```

events() 본문의 큐 소비 로직(133-206)은 변경 없음 — `self._blocks` 를 그대로 쓴다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_microphone_events.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: 회귀 확인 + 커밋**

Run: `python -m pytest tests/ -v`
Expected: 전체 PASS (기존 테스트 포함)

```bash
git add audio_io.py tests/test_microphone_events.py
git commit -m "refactor: Microphone 이 MicRouter 큐를 소비하도록 분리 (VAD 주입 seam 추가)"
```

---

## Task 5: config 설정 추가

**Files:**
- Modify: `config.py`
- Test: `tests/test_remote_mic_config.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_remote_mic_config.py
import importlib
import config


def test_remote_mic_defaults():
    importlib.reload(config)
    assert config.REMOTE_MIC_ENABLED is False
    assert config.REMOTE_MIC_KEY == "jarvis"
    assert config.REMOTE_MIC_IDLE_S == 2.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_remote_mic_config.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'REMOTE_MIC_ENABLED'`

- [ ] **Step 3: config.py 에 추가 (RELAY 설정 근처, 라인 92 부근)**

```python
# 원격 마이크 (웹 프론트가 보내는 외부 마이크 스트림). RELAY_URL/RELAY_TOKEN 재사용.
REMOTE_MIC_ENABLED = os.getenv("REMOTE_MIC_ENABLED", "false").lower() in ("1", "true", "yes")
REMOTE_MIC_KEY = os.getenv("REMOTE_MIC_KEY", "jarvis")   # relay 방 key (캡처 페이지와 일치)
REMOTE_MIC_IDLE_S = float(os.getenv("REMOTE_MIC_IDLE_S", "2.0"))  # 이 시간 무프레임이면 시스템 마이크 복귀
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_remote_mic_config.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add config.py tests/test_remote_mic_config.py
git commit -m "feat: 원격 마이크 config (REMOTE_MIC_ENABLED/KEY/IDLE_S)"
```

---

## Task 6: RemoteMicReceiver — relay 인바운드 mic 수신 클라이언트

relay 의 `/mic-recv/<key>` 에 Bearer 토큰으로 영속 연결한다. binary 프레임은 `router.on_remote_frame()` 으로, JSON 제어는 로그로. 끊기면 지수 백오프 재연결.

**Files:**
- Create: `remote_mic_receiver.py`
- Test: `tests/test_remote_mic_receiver.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_remote_mic_receiver.py
import asyncio
from remote_mic_receiver import RemoteMicReceiver


class FakeRouter:
    def __init__(self):
        self.frames = []
    def on_remote_frame(self, pcm):
        self.frames.append(pcm)


def test_binary_message_goes_to_router_json_logged():
    logs = []
    router = FakeRouter()
    rx = RemoteMicReceiver("ws://x", "tok", router, on_log=logs.append)

    async def main():
        await rx._handle_message(b"\x00\x01\x02\x03")          # binary → router
        await rx._handle_message('{"kind":"no_receiver"}')     # json 제어 → 로그
        await rx._handle_message('not json')                   # 무시(예외 없음)

    asyncio.run(main())
    assert router.frames == [b"\x00\x01\x02\x03"]
    assert any("no_receiver" in m or "수신" in m for m in logs)


def test_recv_url_built_from_base_and_key():
    rx = RemoteMicReceiver("wss://relay.example/", "tok", FakeRouter(),
                           on_log=lambda *_: None, key="room1")
    assert rx._url() == "wss://relay.example/mic-recv/room1"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_remote_mic_receiver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'remote_mic_receiver'`

- [ ] **Step 3: 최소 구현**

```python
# remote_mic_receiver.py
"""relay 의 /mic-recv/<key> 에 붙어 외부 마이크 binary 프레임을 받는 인바운드 클라이언트.

relay_client.py(아웃바운드 publisher)와 대칭. 회의 모드와 독립적으로, REMOTE_MIC_ENABLED
일 때 메인 흐름에서 항상 떠 있는다. 끊기면 지수 백오프로 재연결.
"""
import asyncio
import json
from urllib.parse import quote

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except Exception:  # pragma: no cover
    websockets = None  # type: ignore
    ConnectionClosed = Exception  # type: ignore


class RemoteMicReceiver:
    def __init__(self, url, token, router, *, on_log=print, key=None,
                 connect_timeout=5.0):
        self.base_url = url.rstrip("/")
        self.token = token
        self.router = router
        self.on_log = on_log
        self.key = key
        self.connect_timeout = connect_timeout
        self._stop = asyncio.Event()
        self._task = None

    def _url(self):
        key = quote(self.key or "jarvis", safe="")
        return f"{self.base_url}/mic-recv/{key}"

    async def _handle_message(self, data):
        if isinstance(data, (bytes, bytearray)):
            self.router.on_remote_frame(bytes(data))
            return
        try:
            msg = json.loads(data)
        except Exception:
            return
        kind = msg.get("kind")
        if kind == "no_receiver":
            self.on_log("[mic] relay: 수신자 없음 통지")
        elif kind in ("mic_start", "mic_stop"):
            self.on_log(f"[mic] 원격 캡처 {kind}")

    def start(self):
        if websockets is None:
            self.on_log("[mic] websockets 미설치 — 원격 마이크 비활성")
            return None
        self._task = asyncio.create_task(self._run(), name="remote-mic-rx")
        return self._task

    async def close(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 0.5
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_log(f"[mic] 수신 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _connect_once(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self.on_log("[mic] 원격 마이크 수신 대기 중")
            async for message in ws:
                await self._handle_message(message)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_remote_mic_receiver.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add remote_mic_receiver.py tests/test_remote_mic_receiver.py
git commit -m "feat: RemoteMicReceiver — relay 인바운드 mic 수신 + 백오프 재연결"
```

---

## Task 7: /mic 명령 오버로드 — 소스 전환

기존 `/mic`(무인자 = 듣기 진입)는 유지하고, `phone|remote` / `system|local` / `auto` 인자로 소스 전환을 추가한다.

**Files:**
- Modify: `commands.py:96-104` (`_mic` 핸들러)
- Test: `tests/test_mic_command.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_mic_command.py
import asyncio
import commands


class FakeRouter:
    def __init__(self):
        self.mode = None
    def set_override(self, mode):
        self.mode = mode


def _run(text, ctx):
    asyncio.run(commands.dispatch(text, ctx))


def test_mic_phone_switches_source():
    router = FakeRouter()
    logs = []
    _run("/mic phone", {"log": logs.append, "mic_router": router})
    assert router.mode == "remote"


def test_mic_system_switches_source():
    router = FakeRouter()
    _run("/mic system", {"log": lambda *_: None, "mic_router": router})
    assert router.mode == "local"


def test_mic_no_arg_triggers_wake():
    called = []
    async def trig():
        called.append(True)
    ctx = {"log": lambda *_: None, "trigger_wake": trig, "mic_router": FakeRouter()}
    _run("/mic", ctx)
    assert called == [True]


def test_mic_phone_without_router_warns():
    logs = []
    _run("/mic phone", {"log": logs.append, "mic_router": None})
    assert any("비활성" in m for m in logs)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_mic_command.py -v`
Expected: FAIL — `/mic phone` 가 현재는 trigger_wake 로 빠져 router.mode 미설정

- [ ] **Step 3: commands.py `_mic` 핸들러 교체 (라인 95-104)**

```python
@command("mic", help="듣기 모드 진입 / 마이크 소스 전환", usage="[phone|system|auto]")
async def _mic(args: str, ctx: dict):
    arg = args.strip().lower()
    if arg in ("phone", "remote", "system", "local", "auto"):
        router = ctx.get("mic_router")
        if router is None:
            ctx["log"]("원격 마이크가 비활성화되어 있습니다 (REMOTE_MIC_ENABLED).")
            return
        mode = {"phone": "remote", "remote": "remote",
                "system": "local", "local": "local", "auto": "auto"}[arg]
        router.set_override(mode)
        label = {"remote": "원격(폰)", "local": "시스템", "auto": "자동"}[mode]
        ctx["log"](f"🎙️ 마이크 소스: {label}")
        return
    # 무인자 → 기존 동작(듣기 모드 진입 = 'Hey Jarvis' 와 동일)
    trigger = ctx.get("trigger_wake")
    if trigger is None:
        ctx["log"]("이 환경에서는 마이크 트리거를 사용할 수 없습니다.")
        return
    await trigger()
    ctx["handled_state"] = True
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_mic_command.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add commands.py tests/test_mic_command.py
git commit -m "feat: /mic phone|system|auto 소스 전환 오버로드"
```

---

## Task 8: main.py 배선 — receiver/idle 모니터/캡처 URL 박스

**Files:**
- Modify: `main.py`

테스트가 어려운 통합 배선이라, 변경 후 임포트 스모크 + 수동 확인으로 검증한다.

- [ ] **Step 1: cmd_ctx 에 mic_router 주입 (cmd_ctx 정의 블록, 라인 55 부근에 추가)**

```python
        "mic_router": (mic.router if config.REMOTE_MIC_ENABLED else None),
```

- [ ] **Step 2: receiver + idle 모니터 기동 (player_task 생성 직후, 라인 46 부근에 추가)**

```python
    remote_mic_rx = None
    remote_mic_monitor = None
    if config.REMOTE_MIC_ENABLED and config.RELAY_URL and config.RELAY_TOKEN:
        from remote_mic_receiver import RemoteMicReceiver
        remote_mic_rx = RemoteMicReceiver(
            config.RELAY_URL, config.RELAY_TOKEN, mic.router,
            on_log=console.log, key=config.REMOTE_MIC_KEY,
            connect_timeout=config.RELAY_TIMEOUT_S,
        )
        remote_mic_rx.start()
        remote_mic_monitor = asyncio.create_task(mic.router.run_idle_monitor())
        # 캡처 페이지 URL 박스
        cap_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
        cap_url = f"{cap_base}/capture/{config.REMOTE_MIC_KEY}"
        box_width = max(len(cap_url) + 4, 60)
        border = "─" * box_width
        console.log("")
        console.log(f"┌{border}┐")
        console.log(f"│  📱 원격 마이크 (이 URL 을 폰/타블렛에서 열기)".ljust(box_width + 1) + "│")
        console.log(f"│  {cap_url}".ljust(box_width + 1) + "│")
        console.log(f"└{border}┘")
        console.log("")
```

- [ ] **Step 3: 종료 정리 (finally 블록, `player_task.cancel()` 직후에 추가)**

```python
        if remote_mic_monitor is not None:
            remote_mic_monitor.cancel()
        if remote_mic_rx is not None:
            try:
                await remote_mic_rx.close()
            except Exception:
                pass
```

- [ ] **Step 4: 임포트 스모크 확인**

Run: `python -c "import main, mic_source, remote_mic_receiver; print('ok')"`
Expected: `ok` (구문/임포트 오류 없음)

- [ ] **Step 5: 전체 테스트 + 커밋**

Run: `python -m pytest tests/ -v`
Expected: 전체 PASS

```bash
git add main.py
git commit -m "feat: main 배선 — 원격 마이크 receiver/idle 모니터/캡처 URL 박스"
```

---

## Task 9: meeting-web — mic 송신/수신 라우트 + DO 포워딩

`/mic/<key>`(브라우저 송신)와 `/mic-recv/<key>`(jarvis 수신)를 Bearer 토큰으로 추가하고, DO 가 송신측 binary 를 수신측으로 포워딩한다. `/capture/<key>` 는 캡처 HTML(Task 10).

**Files:**
- Modify: `meeting-web/src/types.ts`, `meeting-web/src/index.ts`, `meeting-web/src/meeting_do.ts`

- [ ] **Step 1: types.ts — mic 제어 kind 추가**

`EventKind` 유니온에 다음을 추가:

```typescript
  | "mic_start"
  | "mic_stop"
  | "no_receiver"
```

- [ ] **Step 2: index.ts — 라우트 추가 (`/publish/:key` 핸들러 아래)**

`forwardToDO` 의 `role` 타입을 확장:

```typescript
function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv", original: Request): Promise<Response> {
```

`MEETING_HTML` 임포트 아래에 캡처 HTML 임포트 추가:

```typescript
import CAPTURE_HTML from "./static/capture.html";
```

라우트 추가:

```typescript
app.get("/capture/:key", (c) => {
  return new Response(CAPTURE_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});

// 토큰 검증 헬퍼 (publish 와 동일 정책)
function requireToken(c: any): boolean {
  const auth = c.req.header("Authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "").trim();
  return !!token && token === c.env.RELAY_TOKEN;
}

app.get("/mic/:key", async (c) => {
  if (!requireToken(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "mic", c.req.raw);
});

app.get("/mic-recv/:key", async (c) => {
  if (!requireToken(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "mic-recv", c.req.raw);
});
```

> 참고: 브라우저 WebSocket 은 커스텀 헤더를 못 보내므로, 캡처 페이지는 토큰을 쿼리(`?token=`)로 보낸다. `/mic/:key` 핸들러에서 헤더가 없으면 쿼리도 허용하도록 `requireToken` 을 보강:

```typescript
function requireToken(c: any): boolean {
  const auth = c.req.header("Authorization") || "";
  const headerTok = auth.replace(/^Bearer\s+/i, "").trim();
  const queryTok = (c.req.query("token") || "").trim();
  const tok = headerTok || queryTok;
  return !!tok && tok === c.env.RELAY_TOKEN;
}
```

- [ ] **Step 3: meeting_do.ts — mic 슬롯 + 포워딩**

클래스 필드에 추가:

```typescript
  private micSender: WebSocket | null = null;
  private micReceiver: WebSocket | null = null;
```

`fetch()` 의 role 분기 확장 — `role !== "publish" && role !== "subscribe"` 검사를 다음으로 교체:

```typescript
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv") {
      return new Response("not found", { status: 404 });
    }
```

그리고 attach 분기:

```typescript
    if (role === "publish") {
      this.attachPublisher(server);
    } else if (role === "subscribe") {
      this.attachViewer(server);
    } else if (role === "mic") {
      this.attachMicSender(server);
    } else {
      this.attachMicReceiver(server);
    }
```

메서드 추가(`attachViewer` 아래):

```typescript
  // --- 원격 마이크: 브라우저 송신 → jarvis 수신 포워딩 ---

  private attachMicSender(ws: WebSocket): void {
    if (this.micSender) {
      try { this.micSender.close(1000, "replaced"); } catch { /* */ }
    }
    this.micSender = ws;
    ws.addEventListener("message", (msg) => {
      const data = msg.data;
      if (!this.micReceiver) {
        this.safeSend(ws, { ts: Date.now() / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
        return;
      }
      try {
        // binary(오디오) 와 string(제어) 둘 다 그대로 전달
        this.micReceiver.send(data as ArrayBuffer | string);
      } catch { /* 수신측 끊김 — 다음 close 에서 정리 */ }
    });
    ws.addEventListener("close", () => { if (this.micSender === ws) this.micSender = null; });
    ws.addEventListener("error", () => { if (this.micSender === ws) this.micSender = null; });
  }

  private attachMicReceiver(ws: WebSocket): void {
    if (this.micReceiver) {
      try { this.micReceiver.close(1000, "replaced"); } catch { /* */ }
    }
    this.micReceiver = ws;
    ws.addEventListener("close", () => { if (this.micReceiver === ws) this.micReceiver = null; });
    ws.addEventListener("error", () => { if (this.micReceiver === ws) this.micReceiver = null; });
  }
```

- [ ] **Step 4: 타입체크**

Run: `cd meeting-web && npm run typecheck`
Expected: 오류 없음 (capture.html 임포트는 Task 10 에서 파일 생성 후 통과 — 이 단계에서 `static.d.ts` 가 `*.html` 모듈을 선언하므로 임포트 자체는 통과)

> capture.html 이 아직 없으면 번들 시 에러가 날 수 있으나 `tsc --noEmit` 타입체크는 통과한다. 런타임 검증은 Task 11.

- [ ] **Step 5: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/src/types.ts meeting-web/src/index.ts meeting-web/src/meeting_do.ts
git commit -m "feat(meeting-web): /mic·/mic-recv 라우트 + DO binary 포워딩"
```

---

## Task 10: meeting-web — 캡처 페이지 (capture.html)

폰/타블렛 브라우저에서 마이크를 잡아 16kHz mono Int16 으로 다운샘플해 `/mic/<key>` 로 binary 송신. iOS Safari 호환을 위해 `ScriptProcessorNode` 사용(토이 범위).

**Files:**
- Create: `meeting-web/src/static/capture.html`

- [ ] **Step 1: capture.html 작성**

```html
<!-- meeting-web/src/static/capture.html -->
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
<title>Jarvis 원격 마이크</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 24px;
         display: flex; flex-direction: column; gap: 16px; align-items: center; }
  h1 { font-size: 18px; margin: 8px 0; }
  button { font-size: 18px; padding: 14px 28px; border-radius: 12px; border: none;
           background: #2563eb; color: #fff; }
  button.off { background: #dc2626; }
  #status { font-size: 14px; opacity: 0.8; }
  #level { width: 240px; height: 12px; background: #ddd; border-radius: 6px; overflow: hidden; }
  #bar { height: 100%; width: 0%; background: #22c55e; }
  input { font-size: 14px; padding: 8px; width: 240px; }
</style>
</head>
<body>
  <h1>📱 Jarvis 원격 마이크</h1>
  <input id="token" type="password" placeholder="relay token" />
  <button id="toggle">마이크 켜기</button>
  <div id="level"><div id="bar"></div></div>
  <div id="status">대기 중</div>
<script>
const TARGET_SR = 16000;
const key = location.pathname.replace(/^\/capture\//, "");
const $ = (id) => document.getElementById(id);
let ws = null, ctx = null, node = null, stream = null, on = false;

function setStatus(s) { $("status").textContent = s; }

function downsample(input, inRate) {
  if (inRate === TARGET_SR) return input;
  const ratio = inRate / TARGET_SR;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) out[i] = input[Math.floor(i * ratio)];
  return out;
}

function floatToInt16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

async function startMic() {
  const token = $("token").value.trim();
  if (!token) { setStatus("토큰을 입력하세요"); return; }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/mic/${encodeURIComponent(key)}?token=${encodeURIComponent(token)}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { ws.send(JSON.stringify({ kind: "mic_start" })); setStatus("● 전송 중"); };
  ws.onclose = () => setStatus("연결 종료");
  ws.onerror = () => setStatus("연결 오류 (토큰 확인)");
  ws.onmessage = (e) => {
    try { const m = JSON.parse(e.data); if (m.kind === "no_receiver") setStatus("⚠ 자비스 미연결"); } catch {}
  };

  stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  const srcNode = ctx.createMediaStreamSource(stream);
  node = ctx.createScriptProcessor(4096, 1, 1);
  srcNode.connect(node);
  node.connect(ctx.destination);   // iOS 에서 onaudioprocess 가 돌게 하려면 연결 필요
  node.onaudioprocess = (ev) => {
    const input = ev.inputBuffer.getChannelData(0);
    // 레벨 미터
    let peak = 0; for (let i = 0; i < input.length; i++) peak = Math.max(peak, Math.abs(input[i]));
    $("bar").style.width = Math.min(100, peak * 140) + "%";
    if (!ws || ws.readyState !== 1) return;
    const ds = downsample(input, ctx.sampleRate);
    ws.send(floatToInt16(ds).buffer);
  };
}

function stopMic() {
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ kind: "mic_stop" })); } catch {}
  if (node) { node.disconnect(); node.onaudioprocess = null; node = null; }
  if (ctx) { ctx.close(); ctx = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  if (ws) { try { ws.close(); } catch {} ws = null; }
  $("bar").style.width = "0%";
  setStatus("정지됨");
}

$("toggle").addEventListener("click", async () => {
  on = !on;
  $("toggle").textContent = on ? "마이크 끄기" : "마이크 켜기";
  $("toggle").classList.toggle("off", on);
  if (on) { try { await startMic(); } catch (e) { setStatus("마이크 권한 실패: " + e.message); on = false; $("toggle").textContent = "마이크 켜기"; $("toggle").classList.remove("off"); } }
  else stopMic();
});
</script>
</body>
</html>
```

- [ ] **Step 2: 타입체크 + 빌드 드라이런**

Run: `cd meeting-web && npm run typecheck`
Expected: 오류 없음

- [ ] **Step 3: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/src/static/capture.html
git commit -m "feat(meeting-web): 원격 마이크 캡처 페이지"
```

---

## Task 11: meeting-web — 통합 검증 스크립트 + 수동 E2E

DO 포워딩(송신 binary → 수신 도달)과 무토큰 거부를 `wrangler dev` 대상으로 검증한다.

**Files:**
- Create: `meeting-web/scripts/mic_relay_check.mjs`

- [ ] **Step 1: 검증 스크립트 작성**

```javascript
// meeting-web/scripts/mic_relay_check.mjs
// 사용법: 터미널 1) cd meeting-web && RELAY_TOKEN=devtoken npm run dev
//         터미널 2) RELAY_TOKEN=devtoken node scripts/mic_relay_check.mjs
import WebSocket from "ws";

const BASE = process.env.BASE || "ws://localhost:8787";
const TOKEN = process.env.RELAY_TOKEN || "devtoken";
const KEY = "checkroom";

function open(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, opts);
    ws.on("open", () => resolve(ws));
    ws.on("error", reject);
  });
}

async function main() {
  // 1) 무토큰 /mic 은 거부되어야
  let rejected = false;
  try { await open(`${BASE}/mic/${KEY}`); }
  catch { rejected = true; }
  console.log("무토큰 거부:", rejected ? "OK" : "FAIL");

  // 2) 수신자(jarvis 모사) 먼저 연결
  const recv = await open(`${BASE}/mic-recv/${KEY}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  const got = new Promise((res) => recv.on("message", (d, isBinary) => res({ isBinary, len: d.length })));

  // 3) 송신자(브라우저 모사) 연결 후 binary 전송
  const send = await open(`${BASE}/mic/${KEY}?token=${TOKEN}`);
  send.send(Buffer.from(new Int16Array([1, 2, 3, 4]).buffer));

  const r = await Promise.race([got, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 3000))]);
  console.log("binary 포워딩:", r.isBinary && r.len === 8 ? "OK" : `FAIL (${JSON.stringify(r)})`);

  recv.close(); send.close();
  process.exit(0);
}
main().catch((e) => { console.error("FAIL", e); process.exit(1); });
```

- [ ] **Step 2: 실행 검증**

터미널 1: `cd meeting-web && RELAY_TOKEN=devtoken npm run dev`
터미널 2: `cd meeting-web && npm i -D ws && RELAY_TOKEN=devtoken node scripts/mic_relay_check.mjs`
Expected 출력:
```
무토큰 거부: OK
binary 포워딩: OK
```

- [ ] **Step 3: 수동 E2E (배포 후)**

1. `cd meeting-web && npm run deploy`
2. jarvis: `REMOTE_MIC_ENABLED=true REMOTE_MIC_KEY=myroom RELAY_URL=wss://<your>.workers.dev RELAY_TOKEN=<token> python main.py`
3. 콘솔의 📱 박스 URL(`https://<your>.workers.dev/capture/myroom`)을 폰에서 열기 → 토큰 입력 → "마이크 켜기"
4. 폰에 대고 "Hey Jarvis" → 자비스가 깨어나고 폰 음성으로 명령 처리됨을 확인
5. 폰 캡처를 끄거나 페이지를 닫으면 `REMOTE_MIC_IDLE_S` 후 시스템 마이크로 복귀 확인
6. `/mic system` → 강제 시스템, `/mic phone` → 강제 원격, `/mic auto` → 자동 동작 확인

- [ ] **Step 4: 커밋**

```bash
cd /Users/oracle/Documents/concode/jarvis
git add meeting-web/scripts/mic_relay_check.mjs
git commit -m "test(meeting-web): mic 포워딩/무토큰 거부 통합 검증 스크립트"
```

---

## Self-Review 결과

**Spec coverage:**
- 마이크 소스 추상화(MicRouter/Local/Remote) → Task 1-4 ✓
- relay 역방향 전송(/mic, /mic-recv, DO 포워딩) → Task 9 ✓
- 캡처 페이지(getUserMedia→16k Int16) → Task 10 ✓
- 자동 전환 + /mic 수동 오버라이드 → Task 2(전환 로직), Task 7(명령), Task 8(idle 모니터) ✓
- 인바운드 수신 + 백오프 → Task 6 ✓
- config(REMOTE_MIC_*) → Task 5 ✓
- idle 폴백/no_receiver/last-wins/무토큰 거부 → Task 2, Task 9, Task 11 ✓
- 출력 경로 예약(결정 C) → 프로토콜에 `mic_start/stop` 양방향 채널 존재(현재 jarvis→폰 미사용), capture.html `ws.onmessage` 스텁 ✓
- 테스트 전략(단위/통합/수동) → Task 1-7 단위, Task 4·11 통합, Task 11 수동 ✓

**연기(스펙대로 범위 밖):** admin 로그인/롤 UI, TTS→폰 출력 구현, 회의 모드 상호작용, dict-web.

**Type consistency:** `MicRouter.set_override(mode)` ('local'|'remote'|'auto'), `on_remote_frame(bytes)`, `RemoteMicSource.feed(bytes)`/`reset()`, `LocalMicSource.start()/stop()` — Task 간 시그니처 일치 확인. relay kind(`mic_start`/`mic_stop`/`no_receiver`) types.ts·capture.html·receiver 일치.

**알려진 한계(문서화):** 결정 A 의 에코(로컬 스피커→폰 마이크 되먹임)는 `is_speaking()` 게이트로 부분 완화. 회의 모드(`/meet`)는 원격 마이크와 독립이며 1차 범위 밖.
