# 일반 대화 스트리밍 STT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 호출어 이후 일반 대화를 RealtimeSTT 로 인식해 조합중("📝") 텍스트를 콘솔·웹 홈 채팅에 실시간 표시하고, 최종 텍스트로 응답한다.

**Architecture:** 회의 모드의 RealtimeSTT 패턴을 본뜬 `StreamingRecognizer`(번역 없음) 신설. main 의 청취 흐름을 RealtimeSTT-driven 으로 — LISTENING 진입 시 `mic.router.set_tap(recognizer.feed_block)` 으로 블록을 연속 피드, partial 콜백 → 콘솔/웹, final 콜백 → 응답. RealtimeSTT 미설치/실패 시 기존 배치 `stt.transcribe` 로 폴백. 웹은 `partial` 을 뷰 인식으로 렌더(홈=조합중 버블).

**Tech Stack:** Python 3.11 (pytest, RealtimeSTT, numpy) · Cloudflare Worker(정적 app.html) · 바닐라 JS.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`.

---

## Task 1: streaming_stt.py — StreamingRecognizer + 단위 테스트

**Files:** Create `streaming_stt.py`, Create `tests/test_streaming_stt.py`

회의 `MeetingSession` 인식부를 본뜬 번역 없는 래퍼. 테스트 위해 `recorder_factory` 주입을 허용(실제 RealtimeSTT 불요).

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_streaming_stt.py`:
```python
# tests/test_streaming_stt.py
import asyncio
import numpy as np
from streaming_stt import StreamingRecognizer


class _FakeRecorder:
    def __init__(self):
        self.fed = []
    def feed_audio(self, pcm, sr):
        self.fed.append((pcm, sr))


def test_feed_block_converts_to_int16_pcm():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.recorder = _FakeRecorder()
    block = np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)
    rx.feed_block(block)
    assert len(rx.recorder.fed) == 1
    pcm_bytes, sr = rx.recorder.fed[0]
    assert sr == 16000
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
    assert pcm[0] == 0 and pcm[1] == 32767 and pcm[2] == -32767


def test_feed_block_noop_without_recorder():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.feed_block(np.zeros(4, dtype=np.float32))   # recorder is None → no error


def test_partial_dedup_and_strip():
    seen = []
    rx = StreamingRecognizer(on_partial=lambda t: seen.append(t), on_final=lambda t: None)
    rx._on_partial("안녕")
    rx._on_partial("안녕")          # dup → skip
    rx._on_partial("  안녕하세요 ")  # strip → new
    rx._on_partial("")             # empty → skip
    assert seen == ["안녕", "안녕하세요"]


def test_final_dispatch_via_queue():
    got = []
    async def run():
        rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: got.append(t))
        rx._loop = asyncio.get_running_loop()
        rx._final_q = asyncio.Queue()
        consumer = asyncio.create_task(rx._consume_finals())
        await rx._final_q.put("최종 텍스트")
        await asyncio.sleep(0.05)
        await rx._final_q.put(None)   # 종료 센티넬
        await consumer
    asyncio.run(run())
    assert got == ["최종 텍스트"]
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_streaming_stt.py -v` → FAIL (ModuleNotFoundError: streaming_stt).

- [ ] **Step 3: 구현** — `streaming_stt.py`:
```python
# streaming_stt.py
"""일반 대화용 스트리밍 STT — RealtimeSTT 래퍼(번역 없음).

live_translate.MeetingSession 의 인식부를 본뜸. mic.router tap 으로 블록을 연속 피드받아
partial(조합중)·final 콜백을 낸다. 회의 통합은 비범위(중복 최소, 안전 우선).
테스트 용이성을 위해 recorder_factory 주입 허용.
"""
import asyncio
import numpy as np


class StreamingRecognizer:
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="ko", on_log=print, recorder_factory=None):
        self.on_partial = on_partial
        self.on_final = on_final
        self.model = model
        self.realtime_model = realtime_model
        self.language = language
        self.log = on_log
        self._recorder_factory = recorder_factory
        self.recorder = None
        self._loop = None
        self._final_q = None
        self._listen_task = None
        self._consumer_task = None
        self._partial_last = ""

    def feed_block(self, block) -> None:
        """mic.router tap 이 매 블록 호출 — float32[-1,1] 16kHz → int16 PCM bytes 주입.
        (float32 를 그대로 feed_audio 에 주면 내부 astype(int16) 로 0 이 됨)"""
        if self.recorder is None:
            return
        pcm16 = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        self.recorder.feed_audio(pcm16, 16000)

    def _on_partial(self, text):
        """RealtimeSTT 스레드에서 호출 — dedup 후 메인 루프로 안전 위탁."""
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_partial, text)
        else:
            self.on_partial(text)

    def _make_recorder(self):
        if self._recorder_factory is not None:
            return self._recorder_factory(self._on_partial)
        from RealtimeSTT import AudioToTextRecorder
        return AudioToTextRecorder(
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

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._final_q = asyncio.Queue()
        self.recorder = self._make_recorder()
        self._consumer_task = asyncio.create_task(self._consume_finals())
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _consume_finals(self):
        while True:
            text = await self._final_q.get()
            if text is None:
                return
            self._partial_last = ""
            if text:
                res = self.on_final(text)
                if asyncio.iscoroutine(res):
                    await res

    async def _listen_loop(self):
        try:
            while True:
                def _final_cb(t):
                    try:
                        self._loop.call_soon_threadsafe(self._final_q.put_nowait, (t or "").strip())
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

    async def close(self):
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
        if self._final_q is not None:
            await self._final_q.put(None)
        if self._consumer_task and not self._consumer_task.done():
            try:
                await asyncio.wait_for(self._consumer_task, timeout=2.0)
            except Exception:
                self._consumer_task.cancel()
        self._consumer_task = None
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_streaming_stt.py -v` (4 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 5: 커밋**
```bash
git add streaming_stt.py tests/test_streaming_stt.py
git commit -m "feat: StreamingRecognizer — 일반 대화용 RealtimeSTT 래퍼 + 테스트"
```

---

## Task 2: main.py — 청취 흐름 RealtimeSTT-driven 화

**Files:** Modify `main.py`

- [ ] **Step 1: recognizer 변수 선언** — `cmd_ctx = { ... }` 딕셔너리 닫힘(123행 `}`) 다음, `def idle():`(125행) 앞에 한 줄 추가. 현재:
```python
        "mic_router": (mic.router if config.REMOTE_MIC_ENABLED else None),
    }

    def idle():
```
교체:
```python
        "mic_router": (mic.router if config.REMOTE_MIC_ENABLED else None),
    }

    recognizer = None   # 일반 대화 스트리밍 STT (없으면 배치 STT 폴백)

    def idle():
```

- [ ] **Step 2: idle 에서 tap 해제** — 현재 `idle()`(125-128행):
```python
    def idle():
        nonlocal state
        state = "WAITING_WAKE"
        console.log("\n🎙️  'Hey Jarvis' 라고 부르거나 아래에 텍스트를 입력하세요.")
```
교체:
```python
    def idle():
        nonlocal state
        state = "WAITING_WAKE"
        if recognizer is not None:
            mic.router.set_tap(None)   # 호출어 대기 — wake 감지가 블록을 받도록
        console.log("\n🎙️  'Hey Jarvis' 라고 부르거나 아래에 텍스트를 입력하세요.")
```

- [ ] **Step 3: enter_listening 에서 tap 설정** — 현재 `enter_listening` 본문(145-148행):
```python
        state = "LISTENING"
        if not MODE.is_translate():
            console.log("🔔 듣고 있어요…")
        watchdog = asyncio.create_task(listen_timeout())
```
교체:
```python
        state = "LISTENING"
        if recognizer is not None and not MODE.is_translate():
            mic.router.set_tap(recognizer.feed_block)   # 블록을 RealtimeSTT 로 연속 피드
        if not MODE.is_translate():
            console.log("🔔 듣고 있어요…")
        watchdog = asyncio.create_task(listen_timeout())
```

- [ ] **Step 4: STT 콜백 + recognizer 생성** — `console.set_escape_handler(on_escape)`(525행) 앞에 삽입. 현재:
```python
    console.set_escape_handler(on_escape)   # Esc → 진행 응답 취소
    idle()
```
교체:
```python
    # --- 일반 대화 스트리밍 STT: partial→콘솔/웹, final→응답 ---
    def _on_stt_partial(text):
        console.set_status(f"📝 {text[:80]}")
        if web_pub is not None:
            web_pub.emit("partial", text)

    async def _respond_voice(text):
        if text:
            intent = mode_intent(text)
            if intent:
                await _handle_mode(intent, text)
                return   # _handle_mode 가 상태(회의/idle) 관리
            await speak_response(text)
        else:
            console.log("🧑 (인식된 음성 없음)")
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
            idle()

    def _on_stt_final(text):
        nonlocal state, response, watchdog
        if state != "LISTENING":
            return   # 응답/대기 중 들어온 stray final 무시
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
        watchdog = None
        mic.router.set_tap(None)   # 응답 중 인식 중단(자기 TTS 에코 방지)
        state = "RESPONDING"
        response = asyncio.create_task(_respond_voice((text or "").strip()))

    try:
        from streaming_stt import StreamingRecognizer
        recognizer = StreamingRecognizer(
            on_partial=_on_stt_partial, on_final=_on_stt_final,
            model=config.MEET_STT_MODEL, realtime_model=config.MEET_STT_REALTIME_MODEL,
            language=config.WHISPER_LANG, on_log=console.log,
        )
        await recognizer.start()
        console.log("🗣️ 스트리밍 STT 준비됨 (호출어 후 실시간 인식)")
    except Exception as e:
        recognizer = None
        console.log(f"스트리밍 STT 비활성 — 배치 STT 폴백: {e}")

    console.set_escape_handler(on_escape)   # Esc → 진행 응답 취소
    idle()
```
(`_on_stt_final` 이 참조하는 `_respond_voice` 는 바로 위 같은 스코프 — 호출 시점에 존재. `recognizer` 는 Step 1 에서 `None` 선언, 여기서 재대입.)

- [ ] **Step 5: 종료 시 recognizer.close** — finally 의 control_rx 정리 다음에 추가. 현재:
```python
        if control_rx is not None:
            try:
                await control_rx.close()
            except Exception:
                pass
```
다음에 추가:
```python
        if recognizer is not None:
            try:
                await recognizer.close()
            except Exception:
                pass
```

- [ ] **Step 6: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 7: 커밋**
```bash
git add main.py
git commit -m "feat: main — 청취 흐름 RealtimeSTT-driven(partial→웹/콘솔, final→응답) + 배치 폴백"
```

---

## Task 3: app.html — 홈 채팅 조합중 버블

**Files:** Modify `jarvis-web/src/static/app.html`

`partial` 을 뷰 인식으로 — 홈 뷰면 `#chat` 에 조합중 user 버블, 회의 뷰면 기존 `#log` draft 카드. `user` 도착 시 조합중 버블을 최종으로 확정.

- [ ] **Step 1: draft 버블 CSS** — `<style>` 의 `.bubble.assistant { ... }` 규칙 다음에 추가:
```css
  .bubble.draft { opacity: 0.6; }
  .bubble.draft::after { content: "▍"; margin-left: 2px; animation: blink 1s steps(2, start) infinite; }
```
(`@keyframes blink` 은 회의 draft 카드용으로 이미 정의돼 있어 재사용.)

- [ ] **Step 2: chatDraft 상태 변수** — 현재:
```js
  let lastRole = null, lastBubble = null;
```
교체:
```js
  let lastRole = null, lastBubble = null, chatDraft = null;
```

- [ ] **Step 3: user 확정 + partial 뷰 인식** — `handle(ev)` 의 현재 두 case:
```js
      case "user": addText("user", ev.text || ""); return;
      case "assistant": addText("assistant", ev.text || ""); return;
```
교체:
```js
      case "user":
        if (chatDraft) {   // 조합중 버블을 최종으로 확정
          chatDraft.classList.remove("draft");
          chatDraft.textContent = ev.text || "";
          lastRole = "user"; lastBubble = chatDraft; chatDraft = null;
          $("chat").scrollTop = $("chat").scrollHeight;
        } else {
          addText("user", ev.text || "");
        }
        return;
      case "assistant": addText("assistant", ev.text || ""); return;
```
그리고 현재 `partial` case:
```js
      case "partial": {
        if (!draftCard) { draftCard = newCard(); draftCard.classList.add("draft"); }
        draftCard.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`;
        scrollIfLocked();
        return;
      }
```
교체:
```js
      case "partial": {
        if (document.body.dataset.view !== "meeting") {   // 홈 채팅 조합중 버블
          if (!chatDraft) {
            chatDraft = document.createElement("div");
            chatDraft.className = "bubble user draft";
            $("chat").appendChild(chatDraft);
          }
          chatDraft.textContent = ev.text || "";
          $("chat").scrollTop = $("chat").scrollHeight;
          return;
        }
        if (!draftCard) { draftCard = newCard(); draftCard.classList.add("draft"); }
        draftCard.innerHTML = `<div class="src">🧑 ${escapeHtml(ev.text || "")}</div>`;
        scrollIfLocked();
        return;
      }
```

- [ ] **Step 4: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/app4.js && node --check /tmp/app4.js && echo "JS OK"` → `JS OK`
`grep -c 'chatDraft\|bubble user draft' jarvis-web/src/static/app.html` → `5` 이상
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(jarvis-web): 홈 채팅 조합중 버블(partial 뷰 인식) + user 확정"
```

---

## Task 4: 검증 + 배포

**Files:** (없음 — 검증·배포만)

워커 로직 변경 없음(`partial` 은 기존 publisher 경로로 흐르고 app.html 만 렌더 변경). 기존 통합 체크는 회귀 확인용으로만 best-effort 실행.

- [ ] **Step 1: 전체 검증** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed; `cd jarvis-web && npm run typecheck` → 오류 없음.

- [ ] **Step 2: best-effort 통합 체크(회귀)** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s, "Ready" 확인) → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 기존 줄 모두 OK 기대 → `pkill -f "wrangler dev"`. 안 뜨면 스킵+사유.

- [ ] **Step 3: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: jarvis **재시작**(streaming recognizer 로드) → "Hey Jarvis" → 말하는 동안 콘솔 `📝 …` + 웹 홈 채팅에 조합중 버블 실시간 → 말 끝 → user 버블 확정 + 응답. 폰 원격 마이크에서도 동일. RealtimeSTT 미설치 환경이면 배치 STT 로 폴백 동작 확인.

---

## Self-Review 결과

**Spec coverage:**
- `StreamingRecognizer`(번역 없는 RealtimeSTT 래퍼, feed_block/partial/final, recorder_factory 주입) → Task 1 ✓
- 청취 흐름 RealtimeSTT-driven(enter_listening tap, idle tap-clear, on_final→응답, on_partial→웹/콘솔) → Task 2 ✓
- 배치 STT 폴백(RealtimeSTT 실패 시 recognizer=None → 기존 respond_flow_audio 경로) → Task 2 Step 4 try/except ✓
- 웹 partial 뷰 인식(홈 조합중 버블) + user 확정 → Task 3 ✓
- 검증·배포·수동 E2E → Task 4 ✓
- 비범위(회의 통합, faster-whisper 제거, draft 정교한 정리) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `StreamingRecognizer(on_partial, on_final, model, realtime_model, language, on_log, recorder_factory)` ↔ Task2 생성 인자 일치. `feed_block`/`_on_partial`/`_consume_finals`/`_final_q`/`_loop`/`recorder` 가 테스트(Task1)와 구현·main 사용에서 일치. main: `recognizer`(Step1 선언 → Step4 대입), `_on_stt_partial`/`_on_stt_final`/`_respond_voice` 일관. 웹: `chatDraft`(Step2 선언 → Step3 사용), `partial`/`user` kind ↔ main `emit("partial")`/`emit("user")`.

**핵심 위험:** (1) 청취가 tap 기반으로 바뀜 — WAITING_WAKE(tap None, wake 감지) ↔ LISTENING(tap=feed_block) ↔ RESPONDING(tap None) 전이를 enter_listening/idle/_on_stt_final 이 일관되게 관리. (2) recognizer 없으면 tap 미설정 → mic.events utterance → 기존 respond_flow_audio(폴백 보존). (3) `_on_stt_final` 은 sync(루프 스레드) — watchdog.cancel()/create_task 만, await 없음. (4) 메모리: RealtimeSTT+faster-whisper 동시 로드(번역 유지). (5) 회의 모드는 별도 tap 소유 — enter_listening 은 회의 중 호출 안 됨, 상호 배타.
```
