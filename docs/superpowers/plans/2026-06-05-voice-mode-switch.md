# 음성 모드 전환 (C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "미팅 모드로 변경해줘" 같은 음성/텍스트 발화로 jarvis 가 회의 모드(`/meet`)에 진입·종료하고, 웹 프론트가 `/{name}/meeting` ↔ `/{name}` 으로 자동 이동한다.

**Architecture:** 새 순수 모듈 `intent.py`의 `mode_intent(text)` 키워드 매처가 STT/입력 텍스트를 "meeting"/"stop"/None 으로 분류. main.py 가 LLM 앞단에서 이를 검사해 매칭 시 회의 시작/종료 + 상시 web_pub 으로 `navigate` 이벤트 발행. 홈/회의 페이지가 navigate 를 받아 `location.href` 이동.

**Tech Stack:** Python 3.11 (pytest) · Cloudflare Worker(TS) · 바닐라 JS.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`. typecheck: `cd jarvis-web && npm run typecheck`.

---

## Task 1: intent.py — mode_intent 키워드 매처

**Files:** `intent.py`(신규), `tests/test_intent.py`(신규)

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_intent.py`:
```python
# tests/test_intent.py
from intent import mode_intent


def test_enter_intents():
    assert mode_intent("미팅모드로 변경해줘") == "meeting"
    assert mode_intent("회의 모드 시작") == "meeting"
    assert mode_intent("회의 들어가자") == "meeting"
    assert mode_intent("meeting 모드로 전환") == "meeting"


def test_stop_intents():
    assert mode_intent("회의 끝내줘") == "stop"
    assert mode_intent("회의 종료") == "stop"
    assert mode_intent("회의 나가자") == "stop"


def test_non_intents():
    assert mode_intent("오늘 회의 자료 요약해줘") is None
    assert mode_intent("안녕 자비스") is None
    assert mode_intent("") is None
    assert mode_intent(None) is None
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_intent.py -v` → FAIL (ModuleNotFoundError: intent).

- [ ] **Step 3: 구현** — `intent.py`:
```python
"""음성/텍스트의 모드 전환 의도 매칭 (키워드 기반, 순수 함수).

회의-명사 + (종료/전환)-동사 가 함께 있을 때만 매칭 → 일반 대화 오탐 최소화.
"""
_MEETING_NOUNS = ("회의", "미팅", "meeting")
_STOP_VERBS = ("끝", "종료", "나가", "중지", "꺼")
_ENTER_VERBS = ("전환", "변경", "시작", "들어가", "열어", "켜", "바꿔")


def mode_intent(text):
    """text → "meeting" | "stop" | None.
    종료 동사를 먼저 검사(예: '회의 끝내줘'). 명사+동사 둘 다 있어야 매칭."""
    t = (text or "").lower()
    if not any(n in t for n in _MEETING_NOUNS):
        return None
    if any(v in t for v in _STOP_VERBS):
        return "stop"
    if any(v in t for v in _ENTER_VERBS):
        return "meeting"
    return None
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_intent.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 5: 커밋**
```bash
git add intent.py tests/test_intent.py
git commit -m "feat: intent.mode_intent — 회의 모드 전환 의도 키워드 매처"
```

---

## Task 2: main.py — 의도 분기 + navigate 발행

**Files:** `main.py`

통합 배선 — import/parse 스모크 + 전체 suite.

- [ ] **Step 1: import 추가**

`main.py` 상단 import 블록(예: `import coach` 아래)에 추가:
```python
from intent import mode_intent
```

- [ ] **Step 2: `_handle_mode` 헬퍼 추가**

`async def respond_flow_audio(audio):` 정의 **바로 위**에 추가:
```python
    async def _handle_mode(intent: str, text: str) -> None:
        """음성/텍스트 모드 전환 처리 + 웹 navigate 발행."""
        console.log(f"🧑 {text}")
        if web_pub is not None:
            web_pub.emit("user", text)
        if intent == "meeting":
            if web_pub is not None:
                web_pub.emit("assistant", "🎤 회의 모드로 전환합니다")
            await start_meeting_setup()
            if web_pub is not None:
                web_pub.emit("navigate", "meeting")
        else:  # "stop"
            await stop_meeting()
            if web_pub is not None:
                web_pub.emit("navigate", "home")
```
(주의: `_handle_mode` 는 `web_pub`/`start_meeting_setup`/`stop_meeting` 클로저를 참조한다 —
모두 `main()` 안에서 `_handle_mode` 정의 시점 이전에 정의/대입돼 있어야 한다. `web_pub` 는
시작부에서 대입됨. `start_meeting_setup`/`stop_meeting` 은 `respond_flow_audio` 보다 아래에
정의되지만, `_handle_mode` 는 *호출 시점*(audio_loop 태스크)에서야 그 이름을 찾으므로
런타임엔 문제없다.)

- [ ] **Step 3: respond_flow_audio 에 의도 분기**

현재:
```python
        if text:
            await speak_response(text)
        else:
            console.log("🧑 (인식된 음성 없음)")
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
            idle()
```
교체:
```python
        if text:
            intent = mode_intent(text)
            if intent:
                await _handle_mode(intent, text)
                return   # 모드 전환이 자체적으로 상태(회의/idle) 를 관리
            await speak_response(text)
        else:
            console.log("🧑 (인식된 음성 없음)")
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
            idle()
```

- [ ] **Step 4: respond_flow_text 에 의도 분기**

현재:
```python
        cmd_ctx["handled_state"] = False
        if commands.is_command(text):
            await commands.dispatch(text, cmd_ctx)
        else:
            await speak_response(text)
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if not cmd_ctx.get("handled_state"):
            idle()
```
교체:
```python
        cmd_ctx["handled_state"] = False
        if commands.is_command(text):
            await commands.dispatch(text, cmd_ctx)
        else:
            intent = mode_intent(text)
            if intent:
                await _handle_mode(intent, text)
                cmd_ctx["handled_state"] = True   # 모드가 상태를 책임 → idle 생략
            else:
                await speak_response(text)
        while player.is_speaking():
            await asyncio.sleep(0.1)
        if not cmd_ctx.get("handled_state"):
            idle()
```

- [ ] **Step 5: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 6: 커밋**
```bash
git add main.py
git commit -m "feat: main — 음성/텍스트 모드 전환 의도 분기 + navigate 발행"
```

---

## Task 3: 웹 navigate — types + home + meeting

**Files:** `jarvis-web/src/types.ts`, `jarvis-web/src/static/home.html`, `jarvis-web/src/static/meeting.html`

- [ ] **Step 1: types.ts — navigate kind**

`EventKind` union 에 추가(`"assistant"` 다음):
```typescript
  | "navigate"
```

- [ ] **Step 2: home.html — navigate 핸들러**

`connect()` 의 message 핸들러에서 `mic_source` 분기 다음에 navigate 분기 추가. 현재:
```javascript
        else if (ev.kind === "mic_source") {
          $("mic-src").textContent = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
          $("mic-src").classList.toggle("remote", ev.source === "remote");
        }
```
다음으로 교체(뒤에 else if 추가):
```javascript
        else if (ev.kind === "mic_source") {
          $("mic-src").textContent = ev.source === "remote" ? "🎚️ 원격(폰)" : "🎚️ 시스템";
          $("mic-src").classList.toggle("remote", ev.source === "remote");
        }
        else if (ev.kind === "navigate" && ev.text === "meeting") {
          location.href = "/" + encodeURIComponent(name) + "/meeting";
        }
```
(`name` 은 home.html IIFE 상단에 이미 정의됨. 홈은 "home" navigate 는 무시 — 이미 홈.)

- [ ] **Step 3: meeting.html — navigate 핸들러 (home 복귀)**

`handle(ev)` switch 의 `case "mic_source": { ... break; }` 다음, `default:` 앞에 추가:
```javascript
      case "navigate": {
        if (ev.text === "home") location.href = "/" + encodeURIComponent(key);
        break;
      }
```
(`key` 는 meeting.html 상단에 이미 정의된 이 페이지의 name. 회의 페이지는 "meeting" navigate 는 무시 — 이미 회의.)

- [ ] **Step 4: 타입체크 + 커밋**

Run: `cd jarvis-web && npm run typecheck` → 오류 없음
Run: `grep -c '"navigate"\|ev.kind === "navigate"\|case "navigate"' jarvis-web/src/types.ts jarvis-web/src/static/home.html jarvis-web/src/static/meeting.html` → 각 1
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts jarvis-web/src/static/home.html jarvis-web/src/static/meeting.html
git commit -m "feat(jarvis-web): navigate 이벤트 — 홈↔회의 페이지 자동 이동"
```

---

## Task 4: 통합 검증 + 배포

**Files:** `jarvis-web/scripts/mic_relay_check.mjs`

- [ ] **Step 1: navigate broadcast 검증 추가**

`mic_relay_check.mjs` 의 `main()`, 최종 cleanup 전에 추가:
```javascript
  // 9) publisher 가 navigate 이벤트를 보내면 viewer 가 받는다
  const navPub = await open(`${BASE}/publish/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });
  const navViewer = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  const navMsg = (async () => {
    for (;;) {
      const m = await nextMsg(navViewer);
      if (m.text && m.text.includes('"navigate"')) return m.text;
    }
  })();
  navPub.send(JSON.stringify({ kind: "navigate", text: "meeting" }));
  const nv = await Promise.race([navMsg, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("navigate broadcast:", nv.includes('"meeting"') ? "OK" : `FAIL (${nv})`);
  navPub.close(); navViewer.close();
```
(`nextMsg`/`fail` 의 실제 시그니처에 맞춰 조정.)

- [ ] **Step 2: best-effort 라이브 런** — `cd jarvis-web && npx wrangler dev --port 8787 &>/tmp/jw.log &`(8-10s) → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 모든 줄 OK 기대(신규 "navigate broadcast" 포함) → `pkill -f wrangler`. 안 뜨면 스킵+사유.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/scripts/mic_relay_check.mjs
git commit -m "test(jarvis-web): navigate broadcast 검증"
```

- [ ] **Step 4: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 폰 홈 → mic-take → "미팅 모드로 변경해줘" → `/Concode/meeting` 이동 + 회의 시작; "회의 끝내줘" → 홈 복귀.

---

## Self-Review 결과

**Spec coverage:**
- `mode_intent` 키워드 매처 → Task 1 ✓
- 음성·텍스트 의도 분기(respond_flow_audio/text) → Task 2 ✓
- enter→start_meeting+navigate meeting / stop→stop_meeting+navigate home → Task 2 ✓
- types navigate, home(→meeting)/meeting(→home) 이동 → Task 3 ✓
- 통합(navigate broadcast) + 수동 E2E + 배포 → Task 4 ✓
- 연기(LLM 툴 의도) → 미구현 ✓

**Type/이름 consistency:** `mode_intent(text)`→"meeting"/"stop"/None ↔ `_handle_mode(intent, text)` ↔ `web_pub.emit("navigate", "meeting"/"home")` ↔ types `navigate` ↔ home `ev.kind==="navigate" && ev.text==="meeting"` / meeting `ev.text==="home"`. `name`(home)/`key`(meeting) 경로 변수 일관.

**핵심 위험:** (1) `_handle_mode` 가 참조하는 start_meeting_setup/stop_meeting 은 정의가 아래지만 호출 시점엔 존재(클로저) — Task2 Step2 주석. (2) 모드 전환 시 respond_flow_audio 는 `return` 으로 FOLLOW_UP/idle 생략, respond_flow_text 는 `handled_state=True` 로 idle 생략 — 회의 상태가 망가지지 않게. (3) 오탐: 명사+동사 동시 요구 — Task1 테스트로 고정.
```
