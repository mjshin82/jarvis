# Deepgram STT 연동 (미팅) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 설정 STT=Deepgram 이면 미팅에서 RealtimeSTT 대신 Deepgram(nova-3, multi) 스트리밍을 쓰고, 자막·번역 흐름은 그대로 재사용한다. 연결 실패 시 RealtimeSTT 폴백.

**Architecture:** 신규 `DeepgramSTT`(raw websockets) 가 interim→on_partial, speech_final→on_final 콜백을 낸다. `MeetingSession` 이 설정에 따라 Deepgram/RealtimeSTT 를 고르고, Deepgram 콜백을 기존 `_emit("partial")`/`_final_q` 시seam에 연결 — `_consume_finals`/`_translate_bg` 는 무변경. jarvis 전용(웹/relay 무변경).

**Tech Stack:** Python(asyncio, websockets, pytest). 새 의존성 없음.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`.

---

## Task 1: config + deepgram_stt.py + 테스트

**Files:** Modify `config.py`; Create `deepgram_stt.py`, `tests/test_deepgram_stt.py`

- [ ] **Step 1: config 키** — `config.py` 의 `DEEPSEEK_BASE_URL = ...`(49행) 다음에 추가:
```python
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
```

- [ ] **Step 2: 실패 테스트** — `tests/test_deepgram_stt.py`:
```python
# tests/test_deepgram_stt.py
import deepgram_stt


def _dg(partials, finals):
    return deepgram_stt.DeepgramSTT(
        "k", on_partial=lambda t: partials.append(t),
        on_final=lambda t: finals.append(t), on_log=lambda *a: None,
    )


def _msg(transcript, is_final=False, speech_final=False):
    return {"type": "Results", "is_final": is_final, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": transcript}]}}


def test_interim_emits_partial():
    p, f = [], []
    dg = _dg(p, f)
    dg._handle_dg_message(_msg("안녕"))
    assert p == ["안녕"] and f == []


def test_final_accumulate_then_speech_final():
    p, f = [], []
    dg = _dg(p, f)
    dg._handle_dg_message(_msg("안녕", is_final=True))
    dg._handle_dg_message(_msg("하세요", is_final=True, speech_final=True))
    assert f == ["안녕 하세요"]
    dg._handle_dg_message(_msg("또", is_final=True, speech_final=True))   # 새 발화
    assert f[-1] == "또"


def test_non_results_and_empty_ignored():
    p, f = [], []
    dg = _dg(p, f)
    dg._handle_dg_message({"type": "Metadata"})
    dg._handle_dg_message(_msg(""))
    assert p == [] and f == []
```

- [ ] **Step 3: 실패 확인** — `.venv/bin/python -m pytest tests/test_deepgram_stt.py -v` → FAIL (ModuleNotFoundError: deepgram_stt).

- [ ] **Step 4: 구현** — `deepgram_stt.py`:
```python
# deepgram_stt.py
"""Deepgram 스트리밍 STT (미팅용). raw websockets 로 nova-3 멀티링구얼 사용.
interim → on_partial, 발화 종료(speech_final) → on_final. RealtimeSTT 대체."""
import asyncio
import json
from urllib.parse import urlencode

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore

_BASE = "wss://api.deepgram.com/v1/listen"


class DeepgramSTT:
    def __init__(self, api_key, *, language="", on_partial, on_final, on_log=print,
                 connect_timeout=5.0):
        self.api_key = api_key
        self.language = language or "multi"   # 한↔영 코드스위칭
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_log = on_log
        self.connect_timeout = connect_timeout
        self._out_q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._task = None
        self._final_parts = []

    def _url(self):
        params = {
            "model": "nova-3",
            "language": self.language,
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            "endpointing": "300",
        }
        return f"{_BASE}?{urlencode(params)}"

    def feed_pcm(self, pcm16: bytes) -> None:
        try:
            self._out_q.put_nowait(pcm16)
        except asyncio.QueueFull:
            pass

    def _handle_dg_message(self, msg) -> None:
        """Deepgram Results 메시지 → on_partial / on_final. (테스트 분리)"""
        if not isinstance(msg, dict) or msg.get("type") != "Results":
            return
        try:
            alt = msg["channel"]["alternatives"][0]
        except (KeyError, IndexError, TypeError):
            return
        text = (alt.get("transcript") or "").strip()
        if msg.get("is_final"):
            if text:
                self._final_parts.append(text)
            if msg.get("speech_final"):
                full = " ".join(self._final_parts).strip()
                self._final_parts = []
                if full:
                    self.on_final(full)
            elif text:
                self.on_partial(" ".join(self._final_parts).strip())
        elif text:
            prefix = " ".join(self._final_parts).strip()
            self.on_partial((prefix + " " + text).strip())

    async def start(self):
        if websockets is None:
            raise RuntimeError("websockets 미설치 — Deepgram 불가")
        self._task = asyncio.create_task(self._run(), name="deepgram-stt")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.connect_timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError("Deepgram 연결 시간 초과")

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
                self.on_log(f"[deepgram] 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _connect_once(self):
        headers = {"Authorization": f"Token {self.api_key}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self._connected.set()
            self.on_log("[deepgram] 연결됨")
            send = asyncio.create_task(self._send_loop(ws))
            recv = asyncio.create_task(self._recv_loop(ws))
            try:
                done, pending = await asyncio.wait({send, recv}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in (send, recv):
                    if not t.done():
                        t.cancel()
                for t in (send, recv):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            for t in done:
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc

    async def _send_loop(self, ws):
        while not self._stop.is_set():
            try:
                pcm = await asyncio.wait_for(self._out_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await ws.send(pcm)

    async def _recv_loop(self, ws):
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self._handle_dg_message(msg)
```

- [ ] **Step 5: 통과 확인** — `.venv/bin/python -m pytest tests/test_deepgram_stt.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed), `.venv/bin/python -c "import deepgram_stt, config; print(config.DEEPGRAM_API_KEY[:4])"` → 키 앞 4자(`5afd`).

- [ ] **Step 6: 커밋**
```bash
git add config.py deepgram_stt.py tests/test_deepgram_stt.py
git commit -m "feat: DeepgramSTT — nova-3 멀티링구얼 스트리밍 클라이언트 + config 키 + 테스트"
```

---

## Task 2: live_translate — MeetingSession Deepgram/RealtimeSTT 선택

**Files:** Modify `live_translate.py`

- [ ] **Step 1: __init__ 에 _dg** — 현재:
```python
        self.recorder = None
        self._loop = None
```
교체:
```python
        self.recorder = None
        self._dg = None        # Deepgram 백엔드(설정 시)
        self._loop = None
```

- [ ] **Step 2: _dg_partial/_dg_final 메서드** — `_on_partial` 메서드 정의 **앞**(또는 바로 뒤)에 추가. `_on_partial` 시그니처 `def _on_partial(self, text: str):` 위에 삽입:
```python
    def _dg_partial(self, text: str) -> None:
        """Deepgram interim — 메인 루프에서 직접 호출(threadsafe 불요)."""
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        self._emit("partial", text)
        self.set_status(f"📝 {text[:80]}")

    def _dg_final(self, text: str) -> None:
        """Deepgram 발화 종료 — 기존 final 큐로(→ source + 번역)."""
        if self._final_q is not None:
            self._final_q.put_nowait((text or "").strip())

```

- [ ] **Step 3: start() 재구성** — 현재 start() 전체(159행 `async def start` ~ 198행 시작 로그)를 교체:
```python
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._final_q = asyncio.Queue()

        # STT 백엔드: 설정 Deepgram(+키) 우선, 실패/미설정 시 RealtimeSTT 폴백
        if settings.get("stt_backend") == "deepgram" and config.DEEPGRAM_API_KEY:
            from deepgram_stt import DeepgramSTT
            try:
                self._dg = DeepgramSTT(
                    config.DEEPGRAM_API_KEY, language=self.language,
                    on_partial=self._dg_partial, on_final=self._dg_final, on_log=self.log,
                )
                await self._dg.start()
                self.recorder = None
                self.log("🎤 회의 STT: Deepgram (nova-3, multi)")
            except Exception as e:
                self._dg = None
                self.log(f"Deepgram 연결 실패 — 로컬 STT 폴백: {e}")

        if self._dg is None:
            from RealtimeSTT import AudioToTextRecorder   # 회의 진입 시에만 import
            wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH)
            rec_kwargs = dict(
                model=self.model,
                realtime_model_type=self.realtime_model,
                enable_realtime_transcription=True,
                on_realtime_transcription_update=self._on_partial,
                language=self.language,
                initial_prompt=wb_prompt,
                initial_prompt_realtime=wb_prompt,
                spinner=False,
                post_speech_silence_duration=0.7,
                silero_sensitivity=0.4,
                webrtc_sensitivity=3,
                device="cpu",
                compute_type="int8",
                level=30,   # WARNING 만
            )
            rec_kwargs["use_microphone"] = False   # jarvis 가 feed_block 으로 먹인다
            self.recorder = AudioToTextRecorder(**rec_kwargs)
            self._listen_task = asyncio.create_task(self._listen_loop())

        # 번역기 + final 소비자는 두 백엔드 공통
        self._setup_translator()
        self._consumer_task = asyncio.create_task(self._consume_finals())
        self.log(f"🎤 회의 모드 시작 (번역: {self._tx_label}). 끝내려면 /stop.")
```

- [ ] **Step 4: feed_block 라우팅** — 현재:
```python
    def feed_block(self, block) -> None:
        """MicRouter tap 이 매 블록 호출 — float32 [-1,1] 16kHz 블록을
        int16 PCM bytes 로 변환해 RealtimeSTT 에 주입.
        (numpy float32 를 그대로 feed_audio 에 주면 astype(int16) 로 0 이 됨)"""
        if self.recorder is None:
            return
        pcm16 = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        self.recorder.feed_audio(pcm16, 16000)
```
교체:
```python
    def feed_block(self, block) -> None:
        """MicRouter tap 이 매 블록 호출 — float32 [-1,1] 16kHz → int16 PCM 으로
        활성 STT 백엔드(Deepgram/RealtimeSTT)에 주입."""
        if self._dg is None and self.recorder is None:
            return
        pcm16 = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        if self._dg is not None:
            self._dg.feed_pcm(pcm16)
        else:
            self.recorder.feed_audio(pcm16, 16000)
```

- [ ] **Step 5: stop() 에 _dg 정리** — 현재 recorder 종료 블록:
```python
        if self.recorder is not None:
            try:
                self.recorder.shutdown()
            except Exception:
                pass
            self.recorder = None
```
**다음**에 추가:
```python
        if self._dg is not None:
            try:
                await self._dg.close()
            except Exception:
                pass
            self._dg = None
```

- [ ] **Step 6: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('live_translate.py').read()); import live_translate; print('ok')"` → `ok`
Run: `grep -c '_dg\b\|DeepgramSTT\|_dg_partial\|_dg_final' live_translate.py` → `6` 이상
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 7: 커밋**
```bash
git add live_translate.py
git commit -m "feat: MeetingSession 이 설정 따라 Deepgram/RealtimeSTT STT 선택 + 폴백"
```

---

## Task 3: 검증 + 마무리 (배포 없음 — jarvis 전용)

**Files:** (없음)

- [ ] **Step 1: 전체 검증** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed. `.venv/bin/python -c "import main; print('import ok')"` → ok.

- [ ] **Step 2: 수동 E2E (jarvis 재시작 필요, 웹 배포 불필요)** — 웹 `+`→⚙️ 설정 → 미팅 STT = **Deepgram** → (저장됨) → jarvis **재시작** → `/meet` → 콘솔에 "🎤 회의 STT: Deepgram (nova-3, multi)" → 말하면 partial 자막 + 발화 끝 source + 번역. 키 틀리면 "Deepgram 연결 실패 — 로컬 STT 폴백" 로그 후 RealtimeSTT 동작. 설정 = 로컬이면 기존 RealtimeSTT.

(이 기능은 jarvis 파이썬만 변경 — Cloudflare 배포 없음.)

---

## Self-Review 결과

**Spec coverage:**
- config DEEPGRAM_API_KEY → Task 1 Step 1 ✓
- DeepgramSTT(connect/feed/recv/재연결/메시지파싱) + 테스트 → Task 1 ✓
- MeetingSession 설정 기반 선택 + 폴백 + feed/stop 라우팅 → Task 2 ✓
- 번역/source 흐름 재사용(_consume_finals 무변경) → Task 2 Step 3(공통) ✓
- nova-3 + multi → Task 1 `_url` ✓
- 검증·수동 E2E(배포 없음) → Task 3 ✓
- 비범위(배치 Deepgram, 일반 대화, 웹) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `_dg`/`_dg_partial`/`_dg_final`/`feed_pcm`/`_handle_dg_message`/`on_partial`/`on_final`
가 deepgram_stt ↔ live_translate 호출과 일치. `settings.get("stt_backend")=="deepgram"` ↔ settings 키.
`config.DEEPGRAM_API_KEY` ↔ config. `_final_q`/`_consume_finals`/`_setup_translator` 공통 경로 재사용.

**핵심 위험:** (1) start() 재구성 — RealtimeSTT import 를 폴백 분기로 옮겨 Deepgram 만 쓸 때 RealtimeSTT
불필요 로드 안 함. `_consume_finals`/번역기는 공통. (2) `_listen_task` 는 RealtimeSTT 일 때만 생성 →
stop() 의 `_listen_task` 취소는 None 가드 있음(기존). (3) Deepgram 연결은 메인 루프 — 콜백 직접 호출
(threadsafe 불요). (4) 연결 실패 → start() 내 try/except 로 폴백(회의 안 깨짐). (5) 무음에도 마이크
tap 이 PCM 을 계속 보내 Deepgram 연결 유지.
