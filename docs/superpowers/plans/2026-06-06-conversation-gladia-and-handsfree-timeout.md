# 일반 대화 Gladia STT + 핸즈프리 30초 타임아웃 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 일반 대화 STT 백엔드로 Gladia 선택 옵션(듣는 동안만 연결, A)과, 웹 핸즈프리 30초 무발화 타임아웃 → 대기복귀 + 웹 마이크 해제(얼럿 없이, B)를 추가한다.

**Architecture:** `ConversationSTT` facade 가 backend(local 상시 / gladia 듣는동안만) 수명주기를 흡수해 컨트롤러에 단일 인터페이스(feed_block/resume/suspend/aclose) 제공. 컨트롤러는 LISTENING 진입 시 resume, IDLE 시 suspend. 핸즈프리 타임아웃은 기존 watchdog 을 30초 cap + `mic_release` 이벤트 발행으로 확장.

**Tech Stack:** Python 3.11 asyncio + pytest(`.venv/bin/python -m pytest`); TS Cloudflare Worker(`npm run typecheck`). 웹 인라인 JS 는 `node --check` 구문검사.

**스펙:** `docs/superpowers/specs/2026-06-06-conversation-gladia-and-handsfree-timeout-design.md`

---

## Part A — 일반 대화 Gladia 옵션

### Task 1: `GladiaSTT.feed_block` 추가

**Files:** Modify `gladia_stt.py`; Test `tests/test_gladia_stt.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_gladia_stt.py` 끝에

```python
def test_feed_block_converts_float32_and_enqueues():
    import numpy as np
    from gladia_stt import GladiaSTT
    g = GladiaSTT("k", on_partial=lambda t: None, on_final=lambda t: None)
    g.feed_block(np.array([0.0, 1.0, -1.0], dtype=np.float32))
    assert g._out_q.qsize() == 1
    arr = np.frombuffer(g._out_q.get_nowait(), dtype="<i2")
    assert arr[1] == 32767 and arr[2] == -32767
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_gladia_stt.py -q`
Expected: FAIL — `AttributeError: 'GladiaSTT' object has no attribute 'feed_block'`

- [ ] **Step 3: 구현** — `gladia_stt.py`

상단 import 에 추가: `from realtime_stt import to_pcm16` (기존 `from ws_backoff import reconnect_loop` 근처).
`feed_pcm` 메서드 바로 아래에 추가:
```python
    def feed_block(self, block) -> None:
        """float32 블록 → int16 PCM 으로 변환해 큐 적재(RealtimeSTTAdapter 와 동일 인터페이스)."""
        self.feed_pcm(to_pcm16(block))
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_gladia_stt.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**
```bash
git add gladia_stt.py tests/test_gladia_stt.py
git commit -m "feat(gladia): feed_block(float32) 추가 — 어댑터와 동일 인터페이스"
```

---

### Task 2: `conversation_stt.py` facade + 설정 키 + 테스트

**Files:**
- Create: `conversation_stt.py`
- Modify: `settings.py`
- Test: `tests/test_conversation_stt.py`

- [ ] **Step 1: settings 키 추가** — `settings.py`

`DEFAULTS` 에 `"conversation_stt_backend": "local",` 추가, `ALLOWED` 에
`"conversation_stt_backend": {"gladia", "local"},` 추가.

- [ ] **Step 2: 실패 테스트 작성** — `tests/test_conversation_stt.py`

```python
import asyncio
from conversation_stt import ConversationSTT


class FakeBackend:
    def __init__(self, name): self.name = name; self.started = 0; self.closed = 0; self.fed = []
    async def start(self): self.started += 1
    async def close(self): self.closed += 1
    def feed_block(self, b): self.fed.append(b)


def _facade(backend="local"):
    state = {"backend": backend}
    local = FakeBackend("local")
    gladias = []
    def make_local(): return local
    def make_gladia():
        g = FakeBackend("gladia"); gladias.append(g); return g
    f = ConversationSTT(make_local=make_local, make_gladia=make_gladia,
                        settings_get=lambda k: state["backend"], on_log=lambda *a: None)
    return f, local, gladias, state


def test_resume_local_uses_local_and_routes_feed():
    async def run():
        f, local, gladias, _ = _facade("local")
        await f.resume()
        f.feed_block(b"x")
        assert local.started == 1 and local.fed == [b"x"] and gladias == []
    asyncio.run(run())


def test_resume_gladia_creates_and_starts_gladia():
    async def run():
        f, local, gladias, _ = _facade("gladia")
        await f.resume()
        f.feed_block(b"y")
        assert len(gladias) == 1 and gladias[0].started == 1 and gladias[0].fed == [b"y"]
        assert local.started == 0
    asyncio.run(run())


def test_suspend_closes_gladia_but_keeps_local():
    async def run():
        f, local, gladias, state = _facade("gladia")
        await f.resume(); await f.suspend()
        assert gladias[0].closed == 1
        state["backend"] = "local"
        await f.resume(); await f.suspend()
        assert local.closed == 0   # 로컬은 상시 유지
    asyncio.run(run())


def test_live_switch_local_to_gladia_keeps_local_open():
    async def run():
        f, local, gladias, state = _facade("local")
        await f.resume()                 # local active
        state["backend"] = "gladia"
        await f.resume()                 # gladia active, local 유지
        assert len(gladias) == 1 and gladias[0].started == 1
        assert local.closed == 0
    asyncio.run(run())


def test_switch_gladia_to_local_closes_gladia():
    async def run():
        f, local, gladias, state = _facade("gladia")
        await f.resume()                 # gladia active
        state["backend"] = "local"
        await f.resume()                 # local active → gladia 닫힘
        assert gladias[0].closed == 1 and local.started == 1
    asyncio.run(run())


def test_feed_block_noop_without_active():
    f, _, _, _ = _facade("local")
    f.feed_block(b"z")   # resume 전 → active 없음, 예외 없음


def test_start_preloads_local_when_default_local():
    async def run():
        f, local, gladias, _ = _facade("local")
        await f.start()
        assert local.started == 1 and gladias == []
    asyncio.run(run())


def test_aclose_closes_active_and_local():
    async def run():
        f, local, gladias, state = _facade("local")
        await f.resume()                 # local
        state["backend"] = "gladia"; await f.resume()   # gladia active, local still alive
        await f.aclose()
        assert gladias[0].closed == 1 and local.closed == 1
    asyncio.run(run())
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation_stt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conversation_stt'`

- [ ] **Step 4: 구현** — `conversation_stt.py`

```python
# conversation_stt.py
"""일반 대화용 STT facade — 설정에 따라 로컬(RealtimeSTT) 또는 Gladia 선택.

로컬: 상시 가동(무료), 부팅/최초 resume 시 1회 start, 이후 유지.
Gladia: 클라우드 과금 → resume(LISTENING 진입) 시 연결, suspend(IDLE) 시 해제.
컨트롤러는 backend 차이를 모르고 feed_block/resume/suspend/aclose 만 쓴다.
"""


class ConversationSTT:
    def __init__(self, *, make_local, make_gladia, settings_get, on_log=print):
        self._make_local = make_local
        self._make_gladia = make_gladia
        self._settings_get = settings_get
        self._log = on_log
        self._local = None          # 상시 유지(지연 생성)
        self._local_started = False
        self._active = None         # 현재 feed 대상 backend 인스턴스
        self._active_kind = None    # "local" | "gladia" | None

    def _backend(self) -> str:
        return self._settings_get("conversation_stt_backend") or "local"

    async def _ensure_local(self):
        if self._local is None:
            self._local = self._make_local()
        if not self._local_started:
            await self._local.start()
            self._local_started = True
        return self._local

    def feed_block(self, block) -> None:
        if self._active is not None:
            self._active.feed_block(block)

    async def start(self) -> None:
        """부팅 — 기본이 local 이면 모델 프리로드(첫 발화 지연 방지)."""
        if self._backend() == "local":
            try:
                await self._ensure_local()
            except Exception as e:
                self._log(f"[stt] 로컬 STT 시작 실패: {e}")

    async def resume(self) -> None:
        """LISTENING 진입 — 설정대로 backend 준비."""
        kind = self._backend()
        if kind == self._active_kind:
            return   # 이미 해당 backend 활성
        # backend 전환 — 이전 active 가 gladia 면 연결 해제(로컬은 유지)
        if self._active_kind == "gladia" and self._active is not None:
            try:
                await self._active.close()
            except Exception:
                pass
            self._active = None
        if kind == "gladia":
            try:
                g = self._make_gladia()
                await g.start()
                self._active = g
                self._active_kind = "gladia"
                self._log("🎤 일반 대화 STT: Gladia")
            except Exception as e:
                self._log(f"[stt] Gladia 시작 실패 — 로컬 폴백: {e}")
                self._active = await self._ensure_local()
                self._active_kind = "local"
        else:
            self._active = await self._ensure_local()
            self._active_kind = "local"

    async def suspend(self) -> None:
        """IDLE 진입 — gladia 면 연결 해제, 로컬이면 상시 유지."""
        if self._active_kind == "gladia" and self._active is not None:
            try:
                await self._active.close()
            except Exception:
                pass
            self._active = None
            self._active_kind = None
        # local: no-op (상시 유지 → 다음 resume 빠름)

    async def aclose(self) -> None:
        if self._active_kind == "gladia" and self._active is not None:
            try:
                await self._active.close()
            except Exception:
                pass
        if self._local is not None and self._local_started:
            try:
                await self._local.close()
            except Exception:
                pass
        self._active = None
        self._active_kind = None
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation_stt.py -q`
Expected: PASS (8 passed)

- [ ] **Step 6: 커밋**
```bash
git add conversation_stt.py settings.py tests/test_conversation_stt.py
git commit -m "feat(conversation-stt): ConversationSTT facade + conversation_stt_backend 설정"
```

---

## Part A+B — 컨트롤러

### Task 3: 컨트롤러 resume/suspend 연동 + 핸즈프리 30초 타임아웃 + config

**Files:**
- Modify: `config.py`, `conversation.py`
- Test: `tests/test_conversation.py`

- [ ] **Step 1: config 추가** — `config.py`

`LISTEN_TIMEOUT_S` 줄 근처에 추가:
```python
HANDS_FREE_TIMEOUT_S = float(os.getenv("HANDS_FREE_TIMEOUT_S", "30.0"))  # 웹 핸즈프리 무발화 상한
```

- [ ] **Step 2: 테스트 fake 보강 + 실패 테스트 추가** — `tests/test_conversation.py`

(a) `make_controller` 의 `FakeRecognizer` 클래스에 async 메서드 추가(컨트롤러가 resume/suspend 를 await 하므로):
```python
class FakeRecognizer:
    def __init__(self): self.fed = []
    def feed_block(self, b): self.fed.append(b)
    async def resume(self): pass
    async def suspend(self): pass
    async def aclose(self): pass
```

(b) `make_controller` 의 `deps` dict 에 `hands_free_timeout_s=30.0,` 추가(생성자 인자 전달).

(c) 파일 끝에 타임아웃 테스트 추가:
```python
def test_hands_free_timeout_releases_web_mic_and_idles():
    async def run():
        c = make_controller(hands_free_timeout_s=0.02, listen_timeout_s=0.02)
        await c.start_listening(hands_free=True)   # → CONVERSING/LISTENING, watchdog armed
        await asyncio.sleep(0.06)                  # watchdog 발화 대기
        assert ("mic_release", "") in c.web_pub.emits
        assert c.mode is Mode.IDLE
    asyncio.run(run())


def test_non_handsfree_timeout_idles_without_mic_release():
    async def run():
        c = make_controller(hands_free_timeout_s=10.0, listen_timeout_s=0.02)
        await c._to_listening(cue=False)           # hands_free=False
        await asyncio.sleep(0.06)
        assert c.mode is Mode.IDLE
        assert ("mic_release", "") not in c.web_pub.emits
    asyncio.run(run())
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'hands_free_timeout_s'` (또는 타임아웃 테스트 실패)

- [ ] **Step 4: 구현** — `conversation.py`

(a) 생성자 시그니처(현재 `follow_up=True, listen_timeout_s=8.0, clock=time.monotonic`)에 `hands_free_timeout_s=30.0,` 추가하고 본문에 `self.hands_free_timeout_s = hands_free_timeout_s` 저장(`self.listen_timeout_s = ...` 근처).

(b) `_to_listening` — `self.phase = Phase.LISTENING` 다음, `self._apply_tap()` 앞에 삽입:
```python
        if self.recognizer is not None:
            await self.recognizer.resume()
```

(c) `_set_idle` — `self._apply_tap()` 다음, 프롬프트 로그 앞에 삽입:
```python
        if self.recognizer is not None:
            await self.recognizer.suspend()
```

(d) `_listen_timeout` 전체 교체:
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
            self.web_pub.emit("mic_release")     # 웹에 마이크 해제 신호(얼럿 없이 버튼만)
        self.log("\n⌛ 입력이 없어 대기 상태로 돌아갑니다.")
        await self._set_idle()
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: PASS (기존 + 신규 2)

- [ ] **Step 6: 전체 회귀 + 커밋**
```bash
.venv/bin/python -m pytest -q
git add config.py conversation.py tests/test_conversation.py
git commit -m "feat(conversation): recognizer resume/suspend + 핸즈프리 30초 타임아웃→mic_release"
```
Expected: 전체 통과(실패 0).

---

### Task 4: main.py 배선 (ConversationSTT + hands_free_timeout_s + aclose)

**Files:** Modify `main.py`

READ main.py 의 recognizer 생성 블록(약 269-279), 컨트롤러 생성(약 312-322), recognizer.start 블록(약 333-340), finally 의 recognizer.close(약 389)를 먼저 확인.

- [ ] **Step 1: recognizer 생성 블록 교체** (현재 `try: from streaming_stt import StreamingRecognizer ... except: recognizer=None` 블록)

```python
    from streaming_stt import StreamingRecognizer
    from conversation_stt import ConversationSTT

    def _make_local():
        return StreamingRecognizer(
            on_partial=lambda t: controller.on_partial(t),
            on_final=lambda t: controller.on_final(t),
            model=config.MEET_STT_MODEL, realtime_model=config.MEET_STT_REALTIME_MODEL,
            language=config.WHISPER_LANG, on_log=console.log,
        )

    def _make_gladia():
        from gladia_stt import GladiaSTT
        langs = [s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()]
        return GladiaSTT(
            config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
            on_partial=lambda t: controller.on_partial(t),
            on_final=lambda t: controller.on_final(t), on_log=console.log,
        )

    recognizer = ConversationSTT(
        make_local=_make_local, make_gladia=_make_gladia,
        settings_get=settings.get, on_log=console.log,
    )
```
(make_local/make_gladia 의 `controller` 참조는 호출 시점(start/resume) 해석 — controller 는 그 전에 생성됨.)

- [ ] **Step 2: 컨트롤러 생성에 인자 추가** — `controller = ConversationController(...)` 호출의 `listen_timeout_s=config.LISTEN_TIMEOUT_S,` 줄 뒤에 추가:
```python
        hands_free_timeout_s=config.HANDS_FREE_TIMEOUT_S,
```

- [ ] **Step 3: recognizer.start 블록 교체** (현재 `if recognizer is not None: try: await recognizer.start() ... except: recognizer=None; controller.recognizer=None`)

```python
    if control_rx is not None:
        control_rx.start()
    await recognizer.start()
    console.log("🗣️ 스트리밍 STT 준비됨 (호출어 후 실시간 인식)")
```
(ConversationSTT.start() 는 내부에서 예외를 잡아 로깅하므로 raise 안 함. recognizer 는 항상 존재.)
※ 단, `control_rx.start()` 가 기존에 이 블록에 함께 있었으면 중복 호출 주의 — 기존 `if control_rx is not None: control_rx.start()` 를 유지하고 recognizer 부분만 위처럼 교체.

- [ ] **Step 4: finally 정리 교체** — `await recognizer.close()` 를 `await recognizer.aclose()` 로.

- [ ] **Step 5: 검증**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 6: 커밋**
```bash
git add main.py
git commit -m "wire(main): ConversationSTT 배선 + hands_free_timeout_s + aclose"
```

---

## Part B — 웹 (mic_release 이벤트 + 설정 행)

### Task 5: types.ts + meeting_do.ts + app.html

**Files:** Modify `jarvis-web/src/types.ts`, `jarvis-web/src/meeting_do.ts`, `jarvis-web/src/static/app.html`

- [ ] **Step 1: types.ts — EventKind 에 mic_release 추가**

`| "mic_source"` 줄 근처(owner 전용 계열)에 추가:
```typescript
  | "mic_release"          // jarvis → owner: 무발화 타임아웃, 웹 마이크 해제 신호
```

- [ ] **Step 2: meeting_do.ts — handlePublisherMessage 에 mic_release 처리**

`navigate` 케이스(`if (msg.kind === "navigate") { this.broadcast(this.buildEvent(msg)); return; }`) 바로 아래에 추가(append 없이 broadcast → 리플레이 미적재, PUBLIC_KINDS 미포함이라 owner 만):
```typescript
    // mic_release: 일시 신호(상태 아님) — owner 에게만 broadcast, replay 미적재.
    if (msg.kind === "mic_release") {
      this.broadcast(this.buildEvent(msg));
      return;
    }
```

- [ ] **Step 3: app.html — 설정 모달에 "일반 대화 STT" 행 추가**

`미팅 STT` sheet-row(`name="set-stt"` 행, `</div>` 로 닫힘) 바로 다음에 추가:
```html
      <div class="sheet-row">
        <div class="sheet-label">일반 대화 STT</div>
        <label><input type="radio" name="set-conv-stt" value="gladia"> Gladia</label>
        <label><input type="radio" name="set-conv-stt" value="local"> 로컬</label>
      </div>
```

- [ ] **Step 4: app.html — fillSettings / curSettings 갱신**

`fillSettings(s)` 의 set-stt 줄 다음에 추가:
```javascript
    document.querySelectorAll('input[name="set-conv-stt"]').forEach((r) => { r.checked = (r.value === s.conversation_stt_backend); });
```
`curSettings()` 의 return 을 교체:
```javascript
  function curSettings() {
    const t = document.querySelector('input[name="set-translate"]:checked');
    const s = document.querySelector('input[name="set-stt"]:checked');
    const c = document.querySelector('input[name="set-conv-stt"]:checked');
    return { translate_backend: t ? t.value : "deepseek", stt_backend: s ? s.value : "gladia",
             conversation_stt_backend: c ? c.value : "local" };
  }
```

- [ ] **Step 5: app.html — handle() 에 mic_release 케이스 추가**

`handle(ev)` 의 switch 에서 `case "mic_source":` 블록 근처에 추가:
```javascript
      case "mic_release":
        voiceOn = false;
        $("voice-toggle").classList.remove("active");
        mic.apply();      // 의도적 중단(gen 가드 → onLost 미발화 → 얼럿 없음)
        return;
```

- [ ] **Step 6: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const body=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(body);}catch(e){console.error('SYNTAX ERR',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
```
Expected: `JS syntax OK`, typecheck 0.

- [ ] **Step 7: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts jarvis-web/src/meeting_do.ts jarvis-web/src/static/app.html
git commit -m "feat(web): mic_release 이벤트 처리 + 일반 대화 STT 설정 행"
```

---

## Task 6: 최종 검증

**Files:** (변경 없음)

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, conversation, conversation_stt, gladia_stt; print('import ok')"
.venv/bin/python -m pytest -q
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과(실패 0), typecheck 0.

- [ ] **Step 2: 수동 (배포/재시작 후)**
- 설정에서 "일반 대화 STT = Gladia" → 음성 대화가 Gladia 로 인식(로그 "🎤 일반 대화 STT: Gladia"), "로컬" → RealtimeSTT.
- 웹 음성 ON → 30초 무발화 → **얼럿 없이** 버튼 자동 off + 대기 복귀(jarvis 로그 "입력이 없어 대기"). LLM 응답/TTS 재생 중에는 타임아웃 안 걸림.
- 일반(로컬 호출어) 무발화는 기존 8초 유지.

---

## 비고
- 기본값(`conversation_stt_backend="local"`)이라 미설정 시 현 동작 보존.
- 배포: jarvis 재시작 + 웹은 `cd jarvis-web && npx wrangler deploy`. origin push 직접.
- Gladia 일반대화는 듣는 동안만 연결(과금 한정). 30초 타임아웃이 연결도 닫음(suspend).
