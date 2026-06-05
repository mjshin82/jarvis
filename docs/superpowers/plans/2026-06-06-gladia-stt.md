# Gladia STT 연동 (Deepgram 대체) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 미팅 STT 를 Deepgram → Gladia(solaria-1, 한↔영 code-switching)로 교체하고 Deepgram 을 완전 제거.

**Architecture:** 신규 `GladiaSTT`(REST init → WebSocket)가 transcript→partial/final 콜백을 기존 `_emit("partial")`/`_final_q` 시seam에 연결(번역 흐름 무변경). 설정 옵션 deepgram→gladia. RealtimeSTT 폴백 유지.

**Tech Stack:** Python(asyncio, websockets, requests, pytest) · 웹 app.html(라디오).

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`. (Gladia API 실연결 검증됨.)

---

## Task 1: gladia_stt.py + config + 의존성 + Deepgram 제거

**Files:** Create `gladia_stt.py`, `tests/test_gladia_stt.py`; Modify `config.py`, `requirements.txt`; Delete `deepgram_stt.py`, `tests/test_deepgram_stt.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_gladia_stt.py`:
```python
# tests/test_gladia_stt.py
import gladia_stt


def _gl(partials, finals):
    return gladia_stt.GladiaSTT("k", on_partial=lambda t: partials.append(t),
                                on_final=lambda t: finals.append(t), on_log=lambda *a: None)


def _msg(text, is_final=False):
    return {"type": "transcript", "data": {"is_final": is_final, "utterance": {"text": text}}}


def test_partial_emits():
    p, f = [], []
    _gl(p, f)._handle_gladia_message(_msg("안녕"))
    assert p == ["안녕"] and f == []


def test_final_emits():
    p, f = [], []
    _gl(p, f)._handle_gladia_message(_msg("안녕하세요", is_final=True))
    assert f == ["안녕하세요"] and p == []


def test_non_transcript_and_empty_ignored():
    p, f = [], []
    gl = _gl(p, f)
    gl._handle_gladia_message({"type": "speech_start"})
    gl._handle_gladia_message(_msg(""))
    assert p == [] and f == []
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_gladia_stt.py -v` → FAIL (ModuleNotFoundError: gladia_stt).

- [ ] **Step 3: 구현** — `gladia_stt.py`:
```python
# gladia_stt.py
"""Gladia 스트리밍 STT (미팅용). 2단계: REST init → WebSocket.
solaria-1 + 한↔영 code-switching. transcript(partial)→on_partial, is_final→on_final. RealtimeSTT 대체."""
import asyncio
import json

import requests

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore

_BASE = "https://api.gladia.io"


class GladiaSTT:
    def __init__(self, api_key, *, model="solaria-1", languages=("ko", "en"),
                 on_partial, on_final, on_log=print, connect_timeout=5.0):
        self.api_key = api_key
        self.model = model or "solaria-1"
        self.languages = list(languages) or ["ko", "en"]
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_log = on_log
        self.connect_timeout = connect_timeout
        self._out_q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._task = None

    def _config(self):
        return {
            "encoding": "wav/pcm",
            "bit_depth": 16,
            "sample_rate": 16000,
            "channels": 1,
            "model": self.model,
            "language_config": {"languages": self.languages, "code_switching": True},
            "messages_config": {
                "receive_partial_transcripts": True,
                "receive_final_transcripts": True,
            },
        }

    def _init_session(self):
        """동기 REST — 스레드에서 호출. 세션 ws url 반환."""
        r = requests.post(
            f"{_BASE}/v2/live?region=us-west",
            headers={"X-Gladia-Key": self.api_key},
            json=self._config(), timeout=self.connect_timeout,
        )
        if not r.ok:
            raise RuntimeError(f"Gladia init {r.status_code}: {(r.text or '')[:200]}")
        return r.json()

    def feed_pcm(self, pcm16: bytes) -> None:
        try:
            self._out_q.put_nowait(pcm16)
        except asyncio.QueueFull:
            pass

    def _handle_gladia_message(self, msg) -> None:
        """Gladia transcript 메시지 → on_partial / on_final. (테스트 분리)"""
        if not isinstance(msg, dict) or msg.get("type") != "transcript":
            return
        data = msg.get("data") or {}
        utt = data.get("utterance") or {}
        text = (utt.get("text") or "").strip()
        if not text:
            return
        if data.get("is_final"):
            self.on_final(text)
        else:
            self.on_partial(text)

    async def start(self):
        if websockets is None:
            raise RuntimeError("websockets 미설치 — Gladia 불가")
        self._task = asyncio.create_task(self._run(), name="gladia-stt")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.connect_timeout + 3)
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError("Gladia 연결 시간 초과")

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
                self.on_log(f"[gladia] 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _connect_once(self):
        resp = await asyncio.to_thread(self._init_session)
        url = resp["url"]
        async with websockets.connect(url, ping_interval=20, ping_timeout=10,
                                      open_timeout=self.connect_timeout) as ws:
            self._connected.set()
            self.on_log(f"[gladia] 연결됨 ({self.model}, {','.join(self.languages)})")
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
            self._handle_gladia_message(msg)
```

- [ ] **Step 4: config 교체** — `config.py` 의 현재(50-54):
```python
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
# 미팅 Deepgram STT 모델/언어 (.env 로 조절). 한국어가 multi 에서 부정확하면
# MEET_DEEPGRAM_LANGUAGE=ko 로 고정 가능(대신 상대 영어는 한글로 오인식될 수 있음).
MEET_DEEPGRAM_MODEL = os.getenv("MEET_DEEPGRAM_MODEL", "nova-2")
MEET_DEEPGRAM_LANGUAGE = os.getenv("MEET_DEEPGRAM_LANGUAGE", "multi")
```
교체:
```python
GLADIA_API_KEY = os.getenv("GLADIA_API_KEY", "")
MEET_GLADIA_MODEL = os.getenv("MEET_GLADIA_MODEL", "solaria-1")
MEET_GLADIA_LANGUAGES = os.getenv("MEET_GLADIA_LANGUAGES", "ko,en")
```

- [ ] **Step 5: 의존성 + Deepgram 파일 삭제**
```bash
cd /Users/oracle/Documents/concode/jarvis
# requirements: deepgram-sdk 제거, requests 보장
grep -v -i 'deepgram-sdk' requirements.txt > /tmp/req && mv /tmp/req requirements.txt
grep -qi '^requests' requirements.txt || echo 'requests' >> requirements.txt
git rm deepgram_stt.py tests/test_deepgram_stt.py
```

- [ ] **Step 6: 통과 확인** — `.venv/bin/python -m pytest tests/test_gladia_stt.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed — deepgram 테스트 삭제됨), `.venv/bin/python -c "import gladia_stt, config; print(config.GLADIA_API_KEY[:4])"` → `ef5d`.

- [ ] **Step 7: 커밋**
```bash
git add gladia_stt.py tests/test_gladia_stt.py config.py requirements.txt
git commit -m "feat: GladiaSTT(solaria-1, 한↔영) 신규 + config + Deepgram 제거"
```

---

## Task 2: settings + live_translate 전환

**Files:** Modify `settings.py`, `tests/test_settings.py`, `live_translate.py`

- [ ] **Step 1: settings 기본/허용** — `settings.py` 현재:
```python
    "stt_backend": "deepgram",         # deepgram | local
```
교체:
```python
    "stt_backend": "gladia",           # gladia | local
```
그리고:
```python
    "stt_backend": {"deepgram", "local"},
```
교체:
```python
    "stt_backend": {"gladia", "local"},
```

- [ ] **Step 2: test_settings 갱신** — `tests/test_settings.py` 의 두 곳 `"deepgram"` → `"gladia"`:
  - `assert settings.DEFAULTS["stt_backend"] == "deepgram"` → `== "gladia"`
  - `assert cur["stt_backend"] == "deepgram"   # 무효값 무시 → 기본 유지` → `== "gladia"`

- [ ] **Step 3: live_translate `_dg` → `_stt` 일반화** — `live_translate.py` 에서 `self._dg` 를 전부 `self._stt` 로 치환(replace_all). (필드·`self._stt_partial`/`self._stt_final` 호출 포함.) 그리고 메서드 정의 2곳:
  - `def _dg_partial(self, text: str) -> None:` → `def _stt_partial(self, text: str) -> None:`
  - `def _dg_final(self, text: str) -> None:` → `def _stt_final(self, text: str) -> None:`
  - line 97 주석 `# Deepgram 백엔드(설정 시)` → `# 스트리밍 STT 백엔드(Gladia)`
  - `_stt_partial`/`_stt_final` docstring 의 "Deepgram" → "Gladia"(또는 일반어). 선택.

- [ ] **Step 4: start() 백엔드 블록 교체** — 현재(167-181):
```python
        # STT 백엔드: 설정 Deepgram(+키) 우선, 실패/미설정 시 RealtimeSTT 폴백
        if settings.get("stt_backend") == "deepgram" and config.DEEPGRAM_API_KEY:
            from deepgram_stt import DeepgramSTT
            try:
                self._stt = DeepgramSTT(
                    config.DEEPGRAM_API_KEY,
                    model=config.MEET_DEEPGRAM_MODEL, language=config.MEET_DEEPGRAM_LANGUAGE,
                    on_partial=self._stt_partial, on_final=self._stt_final, on_log=self.log,
                )
                await self._stt.start()
                self.recorder = None
                self.log(f"🎤 회의 STT: Deepgram ({config.MEET_DEEPGRAM_MODEL}, {config.MEET_DEEPGRAM_LANGUAGE})")
            except Exception as e:
                self._stt = None
                self.log(f"Deepgram 연결 실패 — 로컬 STT 폴백: {e}")
```
(주의: Step 3 의 replace_all 로 `self._dg`→`self._stt` 가 이미 적용된 상태가 위 모습이다.)
교체:
```python
        # STT 백엔드: 설정 Gladia(+키) 우선, 실패/미설정 시 RealtimeSTT 폴백
        if settings.get("stt_backend") == "gladia" and config.GLADIA_API_KEY:
            from gladia_stt import GladiaSTT
            try:
                langs = [s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()]
                self._stt = GladiaSTT(
                    config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
                    on_partial=self._stt_partial, on_final=self._stt_final, on_log=self.log,
                )
                await self._stt.start()
                self.recorder = None
                self.log(f"🎤 회의 STT: Gladia ({config.MEET_GLADIA_MODEL}, {config.MEET_GLADIA_LANGUAGES})")
            except Exception as e:
                self._stt = None
                self.log(f"Gladia 연결 실패 — 로컬 STT 폴백: {e}")
```

- [ ] **Step 5: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('live_translate.py').read()); import live_translate, settings; print('ok', settings.get('stt_backend'))"` → `ok gladia`
Run: `grep -c '_dg\b\|DeepgramSTT\|DEEPGRAM' live_translate.py` → `0`
Run: `grep -c 'GladiaSTT\|_stt_partial\|_stt_final\|self._stt' live_translate.py` → `5` 이상
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 6: 커밋**
```bash
git add settings.py tests/test_settings.py live_translate.py
git commit -m "feat: settings/live_translate 를 Gladia STT 로 전환(_stt 일반화, 기본 gladia)"
```

---

## Task 3: 웹 라디오 + 검증 + 배포

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: STT 라디오 Gladia** — 현재:
```html
        <label><input type="radio" name="set-stt" value="deepgram"> Deepgram</label>
        <label><input type="radio" name="set-stt" value="local"> 로컬</label>
```
교체:
```html
        <label><input type="radio" name="set-stt" value="gladia"> Gladia</label>
        <label><input type="radio" name="set-stt" value="local"> 로컬</label>
```

- [ ] **Step 2: curSettings 기본값** — 현재:
```js
    return { translate_backend: t ? t.value : "deepseek", stt_backend: s ? s.value : "deepgram" };
```
교체:
```js
    return { translate_backend: t ? t.value : "deepseek", stt_backend: s ? s.value : "gladia" };
```

- [ ] **Step 3: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/appg.js && node --check /tmp/appg.js && echo "JS OK"` → `JS OK`
`cd jarvis-web && npm run typecheck` → 오류 없음
`grep -c 'deepgram\|Deepgram' jarvis-web/src/static/app.html` → `0`
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(jarvis-web): 설정 STT 라디오 Deepgram → Gladia"
```

- [ ] **Step 4: 전체 검증 + 배포** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed; `.venv/bin/python -c "import main; print('ok')"` → ok. 그 다음 `cd jarvis-web && npm run deploy`(웹 라디오 변경 반영). 수동 E2E: 웹 `+`→⚙️ 설정 STT=Gladia → jarvis 재시작 → `/meet` → "🎤 회의 STT: Gladia (solaria-1, ko,en)" → 한↔영 자막+번역. 실패 시 "Gladia 연결 실패 — 로컬 STT 폴백".

---

## Self-Review 결과

**Spec coverage:**
- GladiaSTT(REST init→WS, transcript 파싱) + 테스트 → Task 1 ✓
- config GLADIA_* + DEEPGRAM_* 제거 + deepgram 파일 삭제 + 의존성 → Task 1 ✓
- settings gladia 기본/허용 + 테스트 → Task 2 ✓
- live_translate Gladia 전환(_stt 일반화) + 폴백 → Task 2 ✓
- 웹 라디오 Gladia → Task 3 ✓
- 검증·배포 → Task 3 ✓
- 비범위(Gladia 부가기능, 일반대화) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `gladia` 문자열 일치 — settings DEFAULTS/ALLOWED ↔ live_translate `settings.get("stt_backend")=="gladia"` ↔ 웹 radio value ↔ curSettings 기본. `GladiaSTT(api_key, model, languages, on_partial, on_final, on_log)` ↔ live_translate 호출. `_handle_gladia_message`/`feed_pcm`/`start`/`close` ↔ 사용. `_stt`/`_stt_partial`/`_stt_final` 일관. config `GLADIA_API_KEY`/`MEET_GLADIA_MODEL`/`MEET_GLADIA_LANGUAGES`.

**핵심 위험:** (1) `_dg`→`_stt` replace_all 후 Deepgram 블록 교체 — Step 3→4 순서. (2) 기존 setting.yaml "deepgram" 값은 ALLOWED 제외라 load 시 기본 gladia 로 자동 보정. (3) Gladia init 은 동기 requests → `asyncio.to_thread` 로 비차단. (4) 연결 실패 → RealtimeSTT 폴백(try/except). (5) 웹 라디오 변경은 배포 필요(jarvis 는 재시작).
