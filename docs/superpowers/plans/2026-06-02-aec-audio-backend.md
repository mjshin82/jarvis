# AEC 오디오 백엔드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스피커로 나오는 우리 TTS·음악을 마이크 입력에서 제거(AEC)해, 음악 재생 중에도 호출어 감지와 STT가 깨끗하게 동작하도록 한다.

**Architecture:** 오디오 입출력을 `AudioBackend` 인터페이스 뒤로 추상화한다. macOS에서는 Swift 데몬(`audiod`)이 AVAudioEngine + VoiceProcessingIO로 마이크를 정제하고 TTS·음악을 같은 엔진으로 재생(= AEC 참조)한다. 그 외 환경은 기존 sounddevice + Chrome 동작으로 폴백한다. 음악은 Chrome 대신 yt-dlp+ffmpeg로 디코드해 엔진으로 스트리밍한다(오디오 전용).

**Tech Stack:** Python(asyncio, numpy, sounddevice 폴백), Swift(AVFoundation), ffmpeg, yt-dlp. 기준 spec: `docs/superpowers/specs/2026-06-02-aec-audio-backend-design.md`.

---

## File Structure

- `audio_proto.py` (신규) — 데몬 프로토콜 프레이밍(순수 함수, 하드웨어 불필요). 메시지 타입 상수, `encode*`, `FrameDecoder`, `pcm_to_array`.
- `audio_backend.py` (신규) — `AudioBackend` 추상 + `SounddeviceBackend`(기존 동작) + `AECBackend`(데몬 클라이언트) + `make_backend()` 선택 로직.
- `audiod.swift` (신규) — Swift 데몬. AVAudioEngine + VoiceProcessingIO. 마이크 정제 PCM 송출, voice/music 노드 재생.
- `scripts/build_audiod.sh` (신규) — swiftc 빌드 스크립트.
- `audio_io.py` (수정) — `sd.InputStream` → `backend.mic_frames()` 소비.
- `player.py` (수정) — `sd.play/stop` → `backend` voice 메서드 위임하는 얇은 어댑터.
- `music.py` (수정) — Chrome 열기 → `backend.play_music/stop_music` 위임.
- `main.py` (수정) — `make_backend()`로 백엔드 생성·start/close, Microphone/Player에 주입.
- `config.py` (수정) — `AEC`, `AUDIOD_PATH`.
- `.gitignore` (수정) — 컴파일된 `audiod` 바이너리 제외.
- `tests/` (신규) — `test_audio_proto.py`, `test_audio_backend.py`, `fake_daemon.py`, `fake_backend.py`, 기존 로직 테스트 통합.

원칙: VAD·wake·STT·LLM·TTS합성·상태머신 로직은 변경하지 않는다. 오디오 통로만 백엔드로 교체.

---

## Task 1: 프로토콜 프레이밍 (`audio_proto.py`)

**Files:**
- Create: `audio_proto.py`
- Test: `tests/test_audio_proto.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_audio_proto.py
import numpy as np
import audio_proto as p

def test_encode_decode_roundtrip_multiple_frames():
    data = (
        p.encode(p.FLUSH_VOICE)
        + p.encode_event({"voice": "drained"})
        + p.encode_pcm(p.MIC, np.array([0.1, -0.2, 0.3], dtype=np.float32))
    )
    dec = p.FrameDecoder()
    dec.feed(data)
    frames = list(dec)
    assert [f[0] for f in frames] == [p.FLUSH_VOICE, p.EVENT, p.MIC]
    assert frames[0][1] == b""
    assert p.decode_event(frames[1][1]) == {"voice": "drained"}
    np.testing.assert_allclose(p.pcm_to_array(frames[2][1]),
                               np.array([0.1, -0.2, 0.3], dtype=np.float32), rtol=1e-6)

def test_partial_feed_waits_for_full_frame():
    full = p.encode_pcm(p.MIC, np.array([1.0, 2.0], dtype=np.float32))
    dec = p.FrameDecoder()
    dec.feed(full[:3])           # 헤더 일부만
    assert list(dec) == []
    dec.feed(full[3:])           # 나머지
    frames = list(dec)
    assert len(frames) == 1 and frames[0][0] == p.MIC
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_audio_proto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audio_proto'`

- [ ] **Step 3: 최소 구현**

```python
# audio_proto.py
"""데몬 ↔ Python 프로토콜 프레이밍. 순수 함수(하드웨어/IO 없음)."""
import json
import struct

import numpy as np

# 메시지 타입
MIC = 1          # 데몬→: 16kHz mono float32 마이크 블록
EVENT = 2        # 데몬→: JSON 이벤트
PLAY_VOICE = 3   # →데몬: 48kHz mono float32 TTS/효과음
FLUSH_VOICE = 4  # →데몬: voice 중단+비움
PLAY_MUSIC = 5   # →데몬: 48kHz mono float32 음악
STOP_MUSIC = 6   # →데몬: 음악 중단

_HEADER = struct.Struct("<BI")  # type(1B) + length(4B LE)


def encode(mtype: int, payload: bytes = b"") -> bytes:
    return _HEADER.pack(mtype, len(payload)) + payload


def encode_pcm(mtype: int, samples: np.ndarray) -> bytes:
    b = np.ascontiguousarray(samples, dtype="<f4").tobytes()
    return encode(mtype, b)


def encode_event(obj) -> bytes:
    return encode(EVENT, json.dumps(obj).encode("utf-8"))


def decode_event(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def pcm_to_array(payload: bytes) -> np.ndarray:
    return np.frombuffer(payload, dtype="<f4")


class FrameDecoder:
    """바이트를 feed() 하고 iterate 하면 완성된 (type, payload) 프레임을 yield."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data

    def __iter__(self):
        while len(self.buf) >= _HEADER.size:
            mtype, length = _HEADER.unpack_from(self.buf, 0)
            if len(self.buf) < _HEADER.size + length:
                return
            payload = bytes(self.buf[_HEADER.size:_HEADER.size + length])
            del self.buf[:_HEADER.size + length]
            yield mtype, payload
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_audio_proto.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add audio_proto.py tests/test_audio_proto.py
git commit -m "feat(audio): 데몬 프로토콜 프레이밍(audio_proto)"
```

---

## Task 2: AudioBackend 인터페이스 + FakeBackend

**Files:**
- Create: `audio_backend.py` (인터페이스만)
- Create: `tests/fake_backend.py`
- Test: `tests/test_fake_backend.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_fake_backend.py
import asyncio
import numpy as np
from tests.fake_backend import FakeBackend

def test_fake_backend_mic_and_voice():
    async def main():
        be = FakeBackend(mic_script=[np.zeros(512, np.float32), np.ones(512, np.float32)])
        await be.start()
        got = []
        async for blk in be.mic_frames():
            got.append(blk)
        assert len(got) == 2
        await be.play_voice(np.zeros(10, np.float32), 44100)
        assert be.is_speaking() is True
        be.flush_voice()
        assert be.is_speaking() is False
        assert "play" in (await be.play_music("아이유"))
        assert "stop" in (await be.stop_music())
        await be.close()
    asyncio.run(main())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_fake_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: audio_backend`

- [ ] **Step 3: 인터페이스 구현**

```python
# audio_backend.py
"""오디오 입출력 추상화. 구현: SounddeviceBackend(폴백), AECBackend(Swift 데몬)."""
import abc

import numpy as np


class AudioBackend(abc.ABC):
    @abc.abstractmethod
    async def start(self):
        """백엔드 기동(스트림/데몬/내부 태스크 시작)."""

    @abc.abstractmethod
    async def close(self):
        """정리."""

    @abc.abstractmethod
    async def mic_frames(self):
        """async generator → 16kHz mono float32 블록(np.ndarray)."""

    @abc.abstractmethod
    async def play_voice(self, pcm: np.ndarray, sr: int):
        """TTS/효과음 PCM 재생(순서 보장). 즉시 반환."""

    @abc.abstractmethod
    def flush_voice(self):
        """진행/대기 중 voice 재생 즉시 중단+비움(barge-in)."""

    @abc.abstractmethod
    def is_speaking(self) -> bool:
        """voice 재생 중이거나 대기 중이면 True."""

    @abc.abstractmethod
    async def play_music(self, query: str) -> str:
        """검색어로 음악 재생 시작. 상태 텍스트 반환."""

    @abc.abstractmethod
    async def stop_music(self) -> str:
        """음악 중단. 상태 텍스트 반환."""

    @property
    def supports_inapp_audio(self) -> bool:
        """True 면 음악을 엔진 오디오로 재생(AEC 대상), False 면 외부(Chrome)."""
        return False
```

```python
# tests/fake_backend.py
import numpy as np
from audio_backend import AudioBackend


class FakeBackend(AudioBackend):
    """테스트용: 스크립트된 마이크 프레임을 내보내고 호출을 기록."""

    def __init__(self, mic_script=None):
        self.mic_script = list(mic_script or [])
        self._speaking = False
        self.calls = []

    async def start(self):
        self.calls.append("start")

    async def close(self):
        self.calls.append("close")

    async def mic_frames(self):
        for blk in self.mic_script:
            yield blk

    async def play_voice(self, pcm, sr):
        self.calls.append(("play_voice", len(pcm), sr))
        self._speaking = True

    def flush_voice(self):
        self.calls.append("flush_voice")
        self._speaking = False

    def is_speaking(self):
        return self._speaking

    async def play_music(self, query):
        self.calls.append(("play_music", query))
        return f"play: {query}"

    async def stop_music(self):
        self.calls.append("stop_music")
        return "stopped"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_fake_backend.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add audio_backend.py tests/fake_backend.py tests/test_fake_backend.py
git commit -m "feat(audio): AudioBackend 인터페이스 + FakeBackend"
```

---

## Task 3: 설정값 추가 (`config.py`)

**Files:**
- Modify: `config.py`

- [ ] **Step 1: 설정 추가**

`config.py`의 `BROWSER_APP = ...` 줄 아래에 추가:

```python
# AEC 오디오 백엔드 (macOS VoiceProcessingIO)
AEC = os.getenv("AEC", "auto").lower()              # auto | on | off
AUDIOD_PATH = os.getenv("AUDIOD_PATH", "./audiod")  # Swift 데몬 바이너리 경로
AUDIOD_SRC = "audiod.swift"
```

- [ ] **Step 2: 로드 확인**

Run: `.venv/bin/python -c "import config; print(config.AEC, config.AUDIOD_PATH)"`
Expected: `auto ./audiod`

- [ ] **Step 3: 커밋**

```bash
git add config.py
git commit -m "feat(audio): AEC/AUDIOD 설정값 추가"
```

---

## Task 4: SounddeviceBackend (기존 동작 캡슐화)

기존 `audio_io.py`의 마이크 스트림과 `player.py`의 재생, `music.py`의 Chrome 동작을 `SounddeviceBackend`로 옮긴다. 동작은 동일하게 보존.

**Files:**
- Modify: `audio_backend.py`
- Test: `tests/test_sounddevice_backend.py`

- [ ] **Step 1: 실패하는 테스트 작성** (하드웨어 없이 검증 가능한 부분만: 음악 위임 + 인터페이스 충족)

```python
# tests/test_sounddevice_backend.py
import asyncio
import audio_backend as ab

def test_sounddevice_backend_music_delegates_to_music_module(monkeypatch):
    calls = {}
    async def fake_play(q): calls["play"] = q; return f"play:{q}"
    async def fake_stop(): calls["stop"] = True; return "stop"
    monkeypatch.setattr(ab, "_chrome_play_music", fake_play, raising=False)
    monkeypatch.setattr(ab, "_chrome_stop_music", fake_stop, raising=False)

    async def main():
        be = ab.SounddeviceBackend()
        assert be.supports_inapp_audio is False
        assert (await be.play_music("아이유")) == "play:아이유"
        assert (await be.stop_music()) == "stop"
    asyncio.run(main())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_sounddevice_backend.py -v`
Expected: FAIL — `AttributeError: SounddeviceBackend`

- [ ] **Step 3: 구현** (`audio_backend.py`에 추가)

```python
import asyncio
import queue

import config


class SounddeviceBackend(AudioBackend):
    """기존 동작: sd.InputStream 마이크, sd.play voice(순서 보장), Chrome 음악."""

    def __init__(self):
        self._blocks = queue.Queue()
        self._voice_q: asyncio.Queue = None
        self._playing = False
        self._stream = None
        self._worker = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}")
        self._blocks.put(indata[:, 0].copy())

    async def start(self):
        import sounddevice as sd
        self._voice_q = asyncio.Queue()
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE, channels=config.CHANNELS,
            blocksize=config.BLOCK_SIZE, dtype="float32", callback=self._callback,
        )
        self._stream.start()
        self._worker = asyncio.create_task(self._voice_worker())

    async def close(self):
        if self._worker:
            self._worker.cancel()
        if self._stream:
            self._stream.stop(); self._stream.close()

    async def mic_frames(self):
        loop = asyncio.get_running_loop()
        while True:
            block = await loop.run_in_executor(None, self._blocks.get)
            yield block

    async def _voice_worker(self):
        import sounddevice as sd
        while True:
            pcm, sr = await self._voice_q.get()
            if len(pcm):
                self._playing = True
                try:
                    await asyncio.to_thread(lambda: (sd.play(pcm, sr), sd.wait()))
                finally:
                    self._playing = False

    async def play_voice(self, pcm, sr):
        await self._voice_q.put((pcm, sr))

    def flush_voice(self):
        import sounddevice as sd
        while not self._voice_q.empty():
            try:
                self._voice_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        sd.stop()

    def is_speaking(self):
        return self._playing or (self._voice_q is not None and not self._voice_q.empty())

    async def play_music(self, query):
        return await _chrome_play_music(query)

    async def stop_music(self):
        return await _chrome_stop_music()
```

음악 Chrome 함수는 기존 `music.py` 로직을 모듈 함수로 이동(아래 Task 8에서 `music.py`가 이를 호출하도록 정리). 우선 `audio_backend.py`에 임시로 둔다:

```python
async def _chrome_play_music(query: str) -> str:
    from music import chrome_play   # Task 8에서 정의
    return await chrome_play(query)

async def _chrome_stop_music() -> str:
    from music import chrome_stop
    return await chrome_stop()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_sounddevice_backend.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add audio_backend.py tests/test_sounddevice_backend.py
git commit -m "feat(audio): SounddeviceBackend (기존 동작 캡슐화)"
```

---

## Task 5: make_backend 선택 로직

**Files:**
- Modify: `audio_backend.py`
- Test: `tests/test_make_backend.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_make_backend.py
import audio_backend as ab

def test_off_returns_sounddevice(monkeypatch):
    monkeypatch.setattr(ab.config, "AEC", "off")
    assert isinstance(ab.make_backend(), ab.SounddeviceBackend)

def test_auto_non_macos_returns_sounddevice(monkeypatch):
    monkeypatch.setattr(ab.config, "AEC", "auto")
    monkeypatch.setattr(ab.platform, "system", lambda: "Linux")
    assert isinstance(ab.make_backend(), ab.SounddeviceBackend)

def test_auto_macos_with_daemon_returns_aec(monkeypatch):
    monkeypatch.setattr(ab.config, "AEC", "auto")
    monkeypatch.setattr(ab.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ab, "_aec_available", lambda: True)
    assert isinstance(ab.make_backend(), ab.AECBackend)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_make_backend.py -v`
Expected: FAIL — `AttributeError: make_backend`

- [ ] **Step 3: 구현** (`audio_backend.py` 상단 import에 `import platform` 추가, 하단에 추가)

```python
import platform
import shutil


def _aec_available() -> bool:
    """macOS + swiftc(빌드용) 또는 이미 빌드된 바이너리 존재."""
    return bool(shutil.which("swiftc")) or shutil.which("xcrun") is not None


def make_backend() -> AudioBackend:
    mode = config.AEC
    if mode == "off":
        return SounddeviceBackend()
    is_mac = platform.system() == "Darwin"
    if mode == "on":
        if not is_mac:
            raise RuntimeError("AEC=on 이지만 macOS 가 아닙니다.")
        return AECBackend()
    # auto
    if is_mac and _aec_available():
        return AECBackend()
    return SounddeviceBackend()
```

`AECBackend`는 Task 7에서 구현하지만, 이 테스트가 import 가능하도록 **선언만 먼저** 추가(스텁):

```python
class AECBackend(AudioBackend):
    """Swift 데몬 클라이언트. Task 7 에서 구현."""
    pass  # 메서드는 Task 7
```

> 주: 스텁은 추상 메서드 미구현이라 인스턴스화 시 에러가 난다. `test_auto_macos_with_daemon_returns_aec`는 `make_backend()`가 `AECBackend`를 **반환(생성)** 하므로, 이 테스트는 Task 7 완료 후 통과한다. 지금은 `AECBackend`를 `@abc.abstractmethod` 없는 임시 클래스로 두어 생성만 되게 한다(메서드는 `raise NotImplementedError`). Task 7에서 실제 구현으로 교체.

임시 AECBackend(생성만 가능):

```python
class AECBackend(AudioBackend):
    async def start(self): raise NotImplementedError
    async def close(self): ...
    async def mic_frames(self):
        if False:
            yield
    async def play_voice(self, pcm, sr): raise NotImplementedError
    def flush_voice(self): ...
    def is_speaking(self): return False
    async def play_music(self, query): raise NotImplementedError
    async def stop_music(self): raise NotImplementedError
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_make_backend.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add audio_backend.py tests/test_make_backend.py
git commit -m "feat(audio): make_backend 선택/폴백 로직"
```

---

## Task 6: audio_io / player / main 을 백엔드로 배선

**Files:**
- Modify: `audio_io.py`, `player.py`, `main.py`
- Test: `tests/test_pipeline_logic.py` (기존 /tmp 테스트들을 FakeBackend 기반으로 통합)

- [ ] **Step 1: 실패하는 테스트 작성** (호출어 누출 방지 + 재생 중 VAD 억제 회귀, FakeBackend 사용)

```python
# tests/test_pipeline_logic.py
import asyncio
import numpy as np
import audio_io
from tests.fake_backend import FakeBackend

class FakeVAD:
    def __init__(self, script): self.script=list(script); self.i=0; self.threshold=0.5
    def __call__(self, b):
        ev = self.script[self.i] if self.i < len(self.script) else None
        self.i += 1; return ev
    def reset_states(self): pass

S={'start':0.9}; E={'end':1.0}; N=None

async def collect(mic, script, wake_block=None, speaking=False, n=None):
    mic._vad = FakeVAD(script)
    nb = n or len(script)
    be = mic.backend
    be.mic_script = [np.zeros(audio_io.config.BLOCK_SIZE, np.float32) for _ in range(nb)]
    calls={'i':0}
    def wake(b):
        calls['i']+=1; return wake_block is not None and calls['i']==wake_block
    out=[]
    async for kind,_ in mic.events(wake_detect=wake, is_speaking=lambda: speaking):
        out.append(kind)
    return out

def test_wake_discards_pending_utterance():
    async def main():
        be = FakeBackend(); await be.start()
        mic = audio_io.Microphone(be)
        out = await collect(mic, [S,N,E,N], wake_block=2)
        assert "wake" in out and "utterance" not in out
    asyncio.run(main())

def test_normal_capture():
    async def main():
        be = FakeBackend(); await be.start()
        mic = audio_io.Microphone(be)
        out = await collect(mic, [S,N,E], wake_block=None)
        assert out == ["start","utterance"]
    asyncio.run(main())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_pipeline_logic.py -v`
Expected: FAIL — `Microphone()` 가 backend 인자를 안 받음

- [ ] **Step 3: `audio_io.py` 수정** — Microphone 이 backend 로부터 프레임을 받도록

`Microphone.__init__`와 `events()`를 아래로 교체(VAD/wake/억제 로직은 보존, 프레임 출처만 변경):

```python
class Microphone:
    def __init__(self, backend):
        self.backend = backend
        self._vad_model = load_silero_vad()
        self._vad = VADIterator(
            self._vad_model, threshold=config.VAD_THRESHOLD,
            sampling_rate=config.SAMPLE_RATE, min_silence_duration_ms=config.SILENCE_MS,
        )

    async def events(self, wake_detect=None, is_speaking=lambda: False):
        collecting = False
        buffer = []
        async for block in self.backend.mic_frames():
            if wake_detect is not None and wake_detect(block):
                collecting = False; buffer = []
                self._vad.reset_states()
                yield ("wake", None)
                continue
            if is_speaking():
                if collecting:
                    collecting = False; buffer = []
                self._vad.reset_states()
                continue
            event = self._vad(block)
            if event and "start" in event:
                collecting = True; buffer = []
                yield ("start", None)
            if collecting:
                buffer.append(block)
            if event and "end" in event and collecting:
                collecting = False
                self._vad.reset_states()
                yield ("utterance", np.concatenate(buffer))
```

기존 `_callback`, `_blocks`, `sd` import, `sd.InputStream` 코드는 삭제(백엔드가 담당). `import sounddevice`/`import queue` 제거.

> 주의: 기존 `events()`의 마이크 큐 비우기(`self._blocks` drain)는 백엔드 단의 책임으로 옮기지 않는다. wake 시 잔여 폐기는 `collecting/buffer` 리셋 + VAD reset 으로 충분(프레임은 backend 가 실시간 공급하며, FakeBackend/실데몬 모두 호출어 이전 프레임을 따로 버퍼링하지 않음). 실데몬에서 추가 지연이 관찰되면 Task 7에서 데몬측 입력 드롭을 검토.

- [ ] **Step 4: `player.py` 수정** — 백엔드 voice 위임 어댑터로

`player.py` 전체를 교체:

```python
"""voice(TTS·효과음) 재생 어댑터 — 실제 출력은 backend 가 담당."""
import os

import numpy as np
import soundfile as sf


class Player:
    def __init__(self, backend):
        self.backend = backend
        self._fx_cache: dict[str, tuple] = {}

    def is_speaking(self) -> bool:
        return self.backend.is_speaking()

    async def run(self):
        # 재생 루프는 backend 내부에 있음. 인터페이스 호환을 위해 유지(대기만).
        import asyncio
        while True:
            await asyncio.sleep(3600)

    async def enqueue(self, audio: np.ndarray, sr: int):
        await self.backend.play_voice(audio, sr)

    async def enqueue_file(self, path: str):
        if path not in self._fx_cache:
            if not os.path.exists(path):
                self._fx_cache[path] = None
            else:
                audio, sr = sf.read(path, dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                self._fx_cache[path] = (np.ascontiguousarray(audio), sr)
        item = self._fx_cache[path]
        if item is not None:
            await self.backend.play_voice(*item)

    def flush(self):
        self.backend.flush_voice()
```

- [ ] **Step 5: `main.py` 수정** — 백엔드 생성·주입

`main.py`의 컴포넌트 생성부를 아래로 교체:

```python
from audio_backend import make_backend
# ... (기존 import 유지)

async def main():
    backend = make_backend()
    await backend.start()
    mic = Microphone(backend)
    stt = STT()
    llm = LLM()
    tts = TTS()
    player = Player(backend)
    wake = WakeWord()

    await llm.warmup()
    player_task = asyncio.create_task(player.run())
    state = "WAITING_WAKE"
    # ... (이하 상태머신 로직 동일)
```

`finally:` 블록 끝에 `await backend.close()` 추가. `music.py` 호출 부분은 Task 8에서 backend 연결.

- [ ] **Step 6: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_pipeline_logic.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: 폴백 경로 스모크 (실제 마이크, 수동)**

Run: `AEC=off .venv/bin/python main.py`
Expected: `[stt]`/`[wake]` 로그 + `🎙️ 'Hey Jarvis'...` 배너. "Hey Jarvis" → 한국어 명령 → 답변. (기존과 동일 동작) Ctrl+C 종료.

- [ ] **Step 8: 커밋**

```bash
git add audio_io.py player.py main.py tests/test_pipeline_logic.py
git commit -m "refactor(audio): audio_io/player/main 을 AudioBackend 로 배선"
```

---

## Task 7: AECBackend (Python 데몬 클라이언트)

**Files:**
- Modify: `audio_backend.py` (임시 AECBackend → 실제 구현)
- Create: `tests/fake_daemon.py`, `tests/test_aec_backend.py`

- [ ] **Step 1: 가짜 데몬 작성** (Swift 없이 프로토콜만 흉내내는 Python 프로세스)

```python
# tests/fake_daemon.py
"""stdin 프레임을 읽고, MIC 프레임 몇 개와 drained 이벤트를 stdout 으로 보낸다."""
import sys
import numpy as np
import audio_proto as p

def main():
    out = sys.stdout.buffer
    # MIC 프레임 3개 송출
    for _ in range(3):
        out.write(p.encode_pcm(p.MIC, np.zeros(512, np.float32)))
    out.flush()
    dec = p.FrameDecoder()
    while True:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            break
        dec.feed(chunk)
        for mtype, _payload in dec:
            if mtype == p.PLAY_VOICE:
                out.write(p.encode_event({"voice": "drained"})); out.flush()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/test_aec_backend.py
import asyncio
import sys
import numpy as np
import audio_backend as ab

def test_aec_backend_against_fake_daemon():
    async def main():
        be = ab.AECBackend(cmd=[sys.executable, "tests/fake_daemon.py"])
        await be.start()
        frames = []
        async def read_three():
            async for blk in be.mic_frames():
                frames.append(blk)
                if len(frames) == 3:
                    return
        await asyncio.wait_for(read_three(), timeout=5)
        assert len(frames) == 3
        await be.play_voice(np.zeros(48000, np.float32), 48000)  # is_speaking True
        assert be.is_speaking() is True
        await asyncio.sleep(0.3)            # 데몬이 drained 이벤트 보냄
        assert be.is_speaking() is False
        await be.close()
    asyncio.run(main())
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_aec_backend.py -v`
Expected: FAIL — AECBackend 미구현/생성자 인자 불일치

- [ ] **Step 4: AECBackend 구현** (`audio_backend.py`의 임시 AECBackend 를 교체)

```python
import numpy as np
import audio_proto as proto


class AECBackend(AudioBackend):
    def __init__(self, cmd=None):
        self._cmd = cmd            # None 이면 빌드/실행 경로 자동 결정
        self._proc = None
        self._reader_task = None
        self._mic_q: asyncio.Queue = None
        self._dec = proto.FrameDecoder()
        self._voice_active = 0     # 보낸 voice 청크 수 - drained 수
        self._music = None         # (ffmpeg proc, pump task)

    @property
    def supports_inapp_audio(self):
        return True

    def _resolve_cmd(self):
        if self._cmd:
            return self._cmd
        import os
        # 바이너리 없거나 소스보다 오래되면 빌드
        bin_path = config.AUDIOD_PATH
        need_build = (not os.path.exists(bin_path) or
                      os.path.getmtime(config.AUDIOD_SRC) > os.path.getmtime(bin_path))
        if need_build:
            import subprocess
            subprocess.run(["swiftc", config.AUDIOD_SRC, "-o", bin_path,
                            "-framework", "AVFoundation"], check=True)
        return [bin_path]

    async def start(self):
        self._mic_q = asyncio.Queue()
        cmd = self._resolve_cmd()
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        while True:
            chunk = await self._proc.stdout.read(4096)
            if not chunk:
                break
            self._dec.feed(chunk)
            for mtype, payload in self._dec:
                if mtype == proto.MIC:
                    await self._mic_q.put(proto.pcm_to_array(payload).copy())
                elif mtype == proto.EVENT:
                    ev = proto.decode_event(payload)
                    if ev.get("voice") == "drained":
                        self._voice_active = 0

    async def close(self):
        if self._music:
            await self.stop_music()
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    async def mic_frames(self):
        while True:
            yield await self._mic_q.get()

    async def _send(self, data: bytes):
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def play_voice(self, pcm, sr):
        pcm48 = _resample_mono(pcm, sr, 48000)
        self._voice_active += 1
        await self._send(proto.encode_pcm(proto.PLAY_VOICE, pcm48))

    def flush_voice(self):
        self._voice_active = 0
        self._proc.stdin.write(proto.encode(proto.FLUSH_VOICE))

    def is_speaking(self):
        return self._voice_active > 0

    async def play_music(self, query):
        from music import resolve_track, start_ffmpeg_pump
        await self.stop_music()
        track = await resolve_track(query)
        if not track:
            return f"'{query}' 에 맞는 음악을 찾지 못했습니다."
        vid, title = track
        self._music = await start_ffmpeg_pump(vid, self._send_music)
        return f"재생 시작: {title}"

    async def _send_music(self, pcm48: np.ndarray):
        await self._send(proto.encode_pcm(proto.PLAY_MUSIC, pcm48))

    async def stop_music(self):
        self._proc.stdin.write(proto.encode(proto.STOP_MUSIC))
        if self._music:
            from music import stop_ffmpeg_pump
            await stop_ffmpeg_pump(self._music)
            self._music = None
            return "음악을 껐습니다."
        return "재생 중인 음악이 없습니다."
```

리샘플 헬퍼(`audio_backend.py` 하단):

```python
def _resample_mono(pcm: np.ndarray, sr: int, target: int) -> np.ndarray:
    pcm = np.ascontiguousarray(pcm, dtype=np.float32).reshape(-1)
    if sr == target:
        return pcm
    import librosa
    return np.ascontiguousarray(librosa.resample(pcm, orig_sr=sr, target_sr=target),
                                dtype=np.float32)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_aec_backend.py -v`
Expected: PASS

> 주: 이 테스트는 가짜 데몬으로 **프로토콜·클라이언트 로직**만 검증한다. `play_music`은 music 모듈(Task 8) 함수에 의존하므로 이 테스트에선 호출하지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add audio_backend.py tests/fake_daemon.py tests/test_aec_backend.py
git commit -m "feat(audio): AECBackend 데몬 클라이언트(프로토콜/마이크/voice)"
```

---

## Task 8: music.py 재구성 (인앱 ffmpeg 펌프 + Chrome 폴백)

**Files:**
- Modify: `music.py`, `llm.py`(도구 핸들러가 backend 경유), `main.py`(backend 를 music 에 전달)
- Test: `tests/test_music_pump.py`

- [ ] **Step 1: 실패하는 테스트 작성** (가짜 ffmpeg PCM → 펌프가 콜백 호출, stop 시 정리)

```python
# tests/test_music_pump.py
import asyncio
import numpy as np
import music

def test_ffmpeg_pump_feeds_chunks_and_stops(monkeypatch):
    # 가짜 ffmpeg: 48k mono f32 0.2초 분량을 stdout 으로 내보내는 파이썬 프로세스
    import sys
    fake_ffmpeg = [sys.executable, "-c",
        "import sys,numpy as np;"
        "sys.stdout.buffer.write(np.zeros(9600,np.float32).tobytes());"
        "sys.stdout.buffer.flush()"]
    monkeypatch.setattr(music, "_ffmpeg_cmd", lambda vid: fake_ffmpeg)

    got = []
    async def sink(pcm): got.append(len(pcm))

    async def main():
        handle = await music.start_ffmpeg_pump("dummyid", sink)
        await asyncio.sleep(0.5)
        await music.stop_ffmpeg_pump(handle)
        assert sum(got) == 9600   # 전체 샘플 수신
    asyncio.run(main())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_music_pump.py -v`
Expected: FAIL — `music.start_ffmpeg_pump` 없음

- [ ] **Step 3: `music.py` 재구성**

기존 `play_music`/`stop_music`(Chrome)을 `chrome_play`/`chrome_stop`으로 이름 변경하고, 인앱 펌프 함수 추가:

```python
"""음악: 인앱(yt-dlp+ffmpeg→엔진) 또는 Chrome 폴백."""
import asyncio
import shutil
import subprocess

import numpy as np

import config

_CHUNK = 9600  # 0.2s @ 48k mono f32


def _resolve_sync(query: str):
    from yt_dlp import YoutubeDL
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "skip_download": True, "format": "bestaudio/best"}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    entries = (info or {}).get("entries") or []
    if not entries:
        return None
    e = entries[0]
    return e.get("id"), e.get("title", "")


async def resolve_track(query: str):
    return await asyncio.to_thread(_resolve_sync, query)


def _ffmpeg_cmd(vid: str):
    url = f"https://www.youtube.com/watch?v={vid}"
    return ["ffmpeg", "-loglevel", "quiet", "-i", url,
            "-f", "f32le", "-ac", "1", "-ar", "48000", "pipe:1"]
    # 주: 실제로는 yt-dlp 가 추출한 직접 오디오 URL 을 -i 로 주는 것이 안정적.
    #     Task 8 라이브 단계에서 resolve 시 stream url 까지 받아 전달하도록 보강.


async def start_ffmpeg_pump(vid: str, sink):
    """ffmpeg 디코드 → 48k mono f32 청크를 sink(pcm) 코루틴으로 전달. handle 반환."""
    proc = await asyncio.create_subprocess_exec(
        *_ffmpeg_cmd(vid), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def pump():
        nbytes = _CHUNK * 4
        while True:
            data = await proc.stdout.readexactly(nbytes) if False else await proc.stdout.read(nbytes)
            if not data:
                break
            await sink(np.frombuffer(data, dtype="<f4").copy())

    task = asyncio.create_task(pump())
    return (proc, task)


async def stop_ffmpeg_pump(handle):
    proc, task = handle
    task.cancel()
    if proc.returncode is None:
        proc.terminate()


# --- Chrome 폴백 (기존 동작) ---
def _open_in_browser(url: str) -> bool:
    if not shutil.which("open"):
        return False
    try:
        subprocess.run(["open", "-a", config.BROWSER_APP, url], check=True)
    except Exception:
        subprocess.run(["open", url])
    return True


async def chrome_play(query: str) -> str:
    track = await resolve_track(query)
    if not track or not track[0]:
        return f"'{query}' 에 맞는 영상을 찾지 못했습니다."
    vid, title = track
    if _open_in_browser(f"https://www.youtube.com/watch?v={vid}&autoplay=1"):
        return f"재생 시작: {title}"
    return "브라우저를 열지 못했습니다."


def _stop_sync() -> str:
    app = config.BROWSER_APP
    script = f'''
    if application "{app}" is running then
      tell application "{app}"
        set n to 0
        repeat with w in windows
          repeat with t in (tabs of w)
            try
              if (URL of t) contains "youtube.com" then
                close t
                set n to n + 1
              end if
            end try
          end repeat
        end repeat
        return n
      end tell
    else
      return -1
    end if
    '''
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "").strip()
        n = int(out) if out.lstrip("-").isdigit() else 0
    except Exception as e:
        return f"음악을 끄지 못했습니다: {e}"
    if n < 0:
        return "브라우저가 실행 중이 아닙니다."
    return "음악을 껐습니다." if n else "재생 중인 음악이 없습니다."


async def chrome_stop() -> str:
    return await asyncio.to_thread(_stop_sync)
```

- [ ] **Step 4: `llm.py` 도구 핸들러를 backend 경유로**

`LLM.__init__`에 `backend` 인자 추가, `_run_tool` 의 음악 분기를 backend 호출로 변경:

```python
# llm.py
class LLM:
    def __init__(self, backend=None):
        self.backend = backend
        # ... (기존 init)

    async def _run_tool(self, name, args_json):
        try:
            args = json.loads(args_json) if args_json else {}
        except Exception:
            args = {}
        if name == "web_search":
            return await web_search(args.get("query", ""))
        if name == "play_music":
            return await self.backend.play_music(args.get("query", ""))
        if name == "stop_music":
            return await self.backend.stop_music()
        return "지원하지 않는 도구입니다."
```

`from music import play_music, stop_music` import 제거(이제 backend 경유). `main.py`에서 `llm = LLM(backend)` 로 생성.

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_music_pump.py -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add music.py llm.py main.py tests/test_music_pump.py
git commit -m "feat(music): 인앱 ffmpeg 펌프 + backend 경유 재생/중지, Chrome 폴백"
```

---

## Task 9: audiod.swift (Swift 데몬) + 빌드

**Files:**
- Create: `audiod.swift`, `scripts/build_audiod.sh`
- Modify: `.gitignore`

> 이 태스크의 코드는 헤드리스 자동 테스트가 불가하다(마이크/스피커 필요). 완성 코드를 제공하되, AEC 실효성·포맷 변환은 **라이브로 반복 검증**한다.

- [ ] **Step 1: `audiod.swift` 작성**

```swift
// audiod.swift — AVAudioEngine + VoiceProcessingIO 오디오 데몬
// 프로토콜: [type:1][len:4 LE][payload].  타입: MIC=1 EVENT=2 PLAY_VOICE=3
//          FLUSH_VOICE=4 PLAY_MUSIC=5 STOP_MUSIC=6
import AVFoundation
import Foundation

let MIC: UInt8 = 1, EVENT: UInt8 = 2, PLAY_VOICE: UInt8 = 3
let FLUSH_VOICE: UInt8 = 4, PLAY_MUSIC: UInt8 = 5, STOP_MUSIC: UInt8 = 6
let MIC_SR = 16000.0, PLAY_SR = 48000.0

let stdoutFH = FileHandle.standardOutput
let outLock = NSLock()

func send(_ type: UInt8, _ payload: Data) {
    var header = Data([type])
    var len = UInt32(payload.count).littleEndian
    header.append(Data(bytes: &len, count: 4))
    outLock.lock(); stdoutFH.write(header); stdoutFH.write(payload); outLock.unlock()
}
func sendEvent(_ json: String) { send(EVENT, json.data(using: .utf8)!) }

final class Audiod {
    let engine = AVAudioEngine()
    var voice = AVAudioPlayerNode()
    var music = AVAudioPlayerNode()
    let playFmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: PLAY_SR,
                                channels: 1, interleaved: false)!
    let micFmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: MIC_SR,
                               channels: 1, interleaved: false)!
    var voicePending = 0
    let pendLock = NSLock()

    func start() throws {
        let input = engine.inputNode
        try input.setVoiceProcessingEnabled(true)   // AEC/NS/AGC
        let inFmt = input.outputFormat(forBus: 0)
        let conv = AVAudioConverter(from: inFmt, to: micFmt)!

        engine.attach(voice); engine.attach(music)
        engine.connect(voice, to: engine.mainMixerNode, format: playFmt)
        engine.connect(music, to: engine.mainMixerNode, format: playFmt)

        input.installTap(onBus: 0, bufferSize: 1024, format: inFmt) { buf, _ in
            let ratio = MIC_SR / inFmt.sampleRate
            let cap = AVAudioFrameCount(Double(buf.frameLength) * ratio + 64)
            guard let out = AVAudioPCMBuffer(pcmFormat: self.micFmt, frameCapacity: cap)
            else { return }
            var err: NSError?
            var fed = false
            conv.convert(to: out, error: &err) { _, status in
                if fed { status.pointee = .noDataNow; return nil }
                fed = true; status.pointee = .haveData; return buf
            }
            if let ch = out.floatChannelData {
                let n = Int(out.frameLength)
                send(MIC, Data(bytes: ch[0], count: n * 4))
            }
        }
        try engine.start()
        voice.play(); music.play()
    }

    func scheduleVoice(_ pcm: [Float]) {
        guard let b = makeBuffer(pcm) else { return }
        pendLock.lock(); voicePending += 1; pendLock.unlock()
        voice.scheduleBuffer(b) {
            self.pendLock.lock(); self.voicePending -= 1
            let empty = self.voicePending == 0
            self.pendLock.unlock()
            if empty { sendEvent("{\"voice\":\"drained\"}") }
        }
    }
    func scheduleMusic(_ pcm: [Float]) {
        guard let b = makeBuffer(pcm) else { return }
        music.scheduleBuffer(b, completionHandler: nil)
    }
    func makeBuffer(_ pcm: [Float]) -> AVAudioPCMBuffer? {
        guard let b = AVAudioPCMBuffer(pcmFormat: playFmt,
              frameCapacity: AVAudioFrameCount(pcm.count)) else { return nil }
        b.frameLength = AVAudioFrameCount(pcm.count)
        pcm.withUnsafeBufferPointer { src in
            b.floatChannelData![0].update(from: src.baseAddress!, count: pcm.count)
        }
        return b
    }
    func flushVoice() {
        voice.stop(); pendLock.lock(); voicePending = 0; pendLock.unlock(); voice.play()
    }
    func stopMusic() { music.stop(); music.play() }
}

func bytesToFloats(_ d: Data) -> [Float] {
    return d.withUnsafeBytes { raw in Array(raw.bindMemory(to: Float.self)) }
}

// --- stdin 프레임 파서 (백그라운드) ---
let app = Audiod()
do { try app.start() } catch { FileHandle.standardError.write("start error: \(error)\n".data(using:.utf8)!); exit(1) }

let inFH = FileHandle.standardInput
var buf = Data()
while true {
    let chunk = inFH.availableData
    if chunk.isEmpty { break }
    buf.append(chunk)
    while buf.count >= 5 {
        let type = buf[buf.startIndex]
        let len = buf.subdata(in: buf.startIndex+1 ..< buf.startIndex+5)
            .withUnsafeBytes { $0.load(as: UInt32.self).littleEndian }
        if buf.count < 5 + Int(len) { break }
        let payload = buf.subdata(in: buf.startIndex+5 ..< buf.startIndex+5+Int(len))
        buf.removeSubrange(buf.startIndex ..< buf.startIndex+5+Int(len))
        switch type {
        case PLAY_VOICE: app.scheduleVoice(bytesToFloats(payload))
        case FLUSH_VOICE: app.flushVoice()
        case PLAY_MUSIC: app.scheduleMusic(bytesToFloats(payload))
        case STOP_MUSIC: app.stopMusic()
        default: break
        }
    }
}
```

- [ ] **Step 2: 빌드 스크립트 작성**

```bash
# scripts/build_audiod.sh
#!/usr/bin/env bash
set -e
swiftc audiod.swift -o audiod -framework AVFoundation
echo "built ./audiod"
```

```bash
chmod +x scripts/build_audiod.sh
```

- [ ] **Step 3: 빌드 확인**

Run: `bash scripts/build_audiod.sh`
Expected: `built ./audiod` (컴파일 에러 없음). 에러 시 메시지대로 Swift 수정(타입/변환 API).

- [ ] **Step 4: `.gitignore` 에 바이너리 추가**

`.gitignore` 에 추가:
```
# 컴파일된 Swift 데몬 (소스 audiod.swift 만 커밋)
/audiod
```

- [ ] **Step 5: 데몬 단독 스모크 (수동, 마이크 필요)**

Run:
```bash
.venv/bin/python -c "
import asyncio, sys, numpy as np, audio_backend as ab
async def m():
    be=ab.AECBackend(cmd=['./audiod']); await be.start()
    n=0
    async for blk in be.mic_frames():
        n+=1
        if n==5: break
    print('MIC 프레임 수신 OK', blk.shape, blk.dtype)
    await be.play_voice((0.2*np.sin(2*np.pi*440*np.arange(48000)/48000)).astype('float32'),48000)
    await asyncio.sleep(1.5)   # 440Hz 1초 들려야
    await be.close()
asyncio.run(m())"
```
Expected: 스피커에서 440Hz 톤 1초. 콘솔에 `MIC 프레임 수신 OK (512,) float32`.

- [ ] **Step 6: 커밋**

```bash
git add audiod.swift scripts/build_audiod.sh .gitignore
git commit -m "feat(audio): audiod.swift (VoiceProcessingIO 데몬) + 빌드 스크립트"
```

---

## Task 10: 라이브 통합 검증 (AEC 효과)

**Files:** 없음(검증만). 발견된 이슈는 해당 Task 로 돌아가 수정.

- [ ] **Step 1: 자동 테스트 전체 통과 확인**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 모든 테스트 PASS (audio_proto, fake_backend, sounddevice_backend, make_backend, pipeline_logic, aec_backend, music_pump)

- [ ] **Step 2: AEC 기동 + 폴백 로그 확인**

Run: `AEC=on .venv/bin/python main.py`
Expected: 데몬 자동 빌드(최초) 후 `🎙️ 'Hey Jarvis'...` 배너. 에러 시 stderr 확인.

- [ ] **Step 3: AEC 효과 1 — TTS 자기 에코**

자비스가 길게 말하는 동안 가만히 있기 → 자기 목소리에 끌려 끊기거나 오인하지 않아야 함(에코 제거 확인).

- [ ] **Step 4: AEC 효과 2 — 음악 분리 (핵심 목표)**

"Hey Jarvis → 아이유 좋은날 틀어줘" → 음악 재생(인앱). 음악이 나오는 중에 "Hey Jarvis → 꺼줘":
- 음악 위에서 호출어가 감지되는지
- "꺼줘" STT 가 음악에 오염되지 않고 정확한지
- stop 시 음악이 멈추는지

Expected: 음악 중에도 호출·명령이 깨끗. (이게 안 되면 Task 9 데몬의 AEC 설정/포맷 점검, 또는 입력/출력 포맷·게인 조정.)

- [ ] **Step 5: 폴백 회귀 확인**

Run: `AEC=off .venv/bin/python main.py`
Expected: 기존 동작(sounddevice + Chrome 음악) 정상.

- [ ] **Step 6: 결과를 spec 에 반영(필요시) + 최종 커밋**

라이브에서 조정한 값(버퍼 크기, 게인, 포맷 등)이 있으면 커밋:
```bash
git add -A && git commit -m "fix(audio): 라이브 AEC 튜닝"
```

---

## Self-Review 메모

- **Spec 커버리지**: 인터페이스(T2)·프로토콜(T1)·SounddeviceBackend/폴백(T4,T5)·배선(T6)·AECBackend(T7)·인앱음악(T8)·Swift데몬/빌드(T9)·테스트/라이브(T10) — spec 전 항목 대응.
- **타입 일관성**: 메시지 상수(MIC/EVENT/PLAY_VOICE/FLUSH_VOICE/PLAY_MUSIC/STOP_MUSIC)는 `audio_proto` 한 곳 정의, Swift 상수와 값 일치(1~6). 포맷: 마이크 16k mono f32, 재생 48k mono f32 — 전 태스크 동일.
- **알려진 라이브 의존**: T9(Swift AEC), T10 효과는 마이크/스피커 필수. T8의 `_ffmpeg_cmd` 는 watch URL 직접 입력으로 시작하되, 불안정하면 resolve 단계에서 yt-dlp 추출 오디오 URL 을 ffmpeg `-i` 로 넘기도록 보강(주석 명시).
- **위험**: ffmpeg에 watch URL 직접 입력은 환경에 따라 실패 가능 → resolve_track 이 (id,title) 외 stream_url 도 반환하도록 T8 라이브에서 확장 여지.
