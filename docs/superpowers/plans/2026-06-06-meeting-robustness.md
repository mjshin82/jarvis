# 미팅 견고성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 미팅 시작 로딩 오버레이(F1), jarvis 재시작 시 마지막 지속모드 복구(F2), 서버다운 웹 표시+음성비활성(F3), 웹 재배포 시 회의뷰 복원(F4).

**Architecture:** jarvis 가 전이마다 마지막 모드를 `.jarvis_state.json` 에 저장하고 부팅 시 복구. DO 가 `currentView`/publisher 부재를 재접속 owner 에 동기화. 웹은 `hello`/`publisher_disconnected` 로 서버 생존을 추적해 표시·음성버튼을 토글하고, 미팅 진입 동안 로딩 오버레이를 띄운다.

**Tech Stack:** Python 3.11 asyncio + pytest; TS Worker(`npm run typecheck`); 웹 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-06-meeting-robustness-design.md`

---

## F2 — jarvis 마지막 모드 복구

### Task 1: `runtime_state.py` + 테스트 + .gitignore

**Files:** Create `runtime_state.py`, `tests/test_runtime_state.py`; Modify `.gitignore`

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_runtime_state.py`

```python
import runtime_state


def test_save_load_roundtrip(tmp_path):
    runtime_state._last = None              # dedupe 상태 초기화
    p = str(tmp_path / "s.json")
    runtime_state.save_mode("meeting", path=p)
    assert runtime_state.load_mode(path=p) == "meeting"


def test_load_default_idle_when_missing(tmp_path):
    assert runtime_state.load_mode(path=str(tmp_path / "none.json")) == "idle"


def test_invalid_mode_not_written(tmp_path):
    runtime_state._last = None
    p = str(tmp_path / "s.json")
    runtime_state.save_mode("bogus", path=p)
    assert runtime_state.load_mode(path=p) == "idle"   # 기록 안 됨 → 기본값


def test_dedupe_skips_unchanged(tmp_path):
    runtime_state._last = None
    p = str(tmp_path / "s.json")
    runtime_state.save_mode("translate", path=p)
    import os
    mtime1 = os.path.getmtime(p)
    runtime_state.save_mode("translate", path=p)   # 동일값 → 재기록 안 함
    assert os.path.getmtime(p) == mtime1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_runtime_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime_state'`

- [ ] **Step 3: 구현** — `runtime_state.py`

```python
# runtime_state.py
"""jarvis 런타임 상태 영속 — 재시작 시 마지막 지속 모드(회의/번역) 복구용.
setting.yaml(사용자 설정)과 별개. gitignore 된 .jarvis_state.json."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".jarvis_state.json")
_ALLOWED = {"idle", "meeting", "translate"}
_last = None   # 중복 기록 방지(같은 값 연속 저장 스킵)


def save_mode(mode: str, path: str = None) -> None:
    global _last
    if mode not in _ALLOWED or mode == _last:
        return
    _last = mode
    try:
        with open(path or PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": mode}, f)
    except Exception:
        pass


def load_mode(path: str = None) -> str:
    p = path or PATH
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                m = (json.load(f) or {}).get("mode")
            if m in _ALLOWED:
                return m
    except Exception:
        pass
    return "idle"
```

- [ ] **Step 4: .gitignore 에 추가** — `.gitignore` 끝에 `.jarvis_state.json` 한 줄 추가.

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_runtime_state.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: 커밋**
```bash
git add runtime_state.py tests/test_runtime_state.py .gitignore
git commit -m "feat(runtime-state): 마지막 모드 영속(save/load) + gitignore"
```

---

### Task 2: 컨트롤러 persist_mode 주입 + 전이별 호출

**Files:** Modify `conversation.py`; Test `tests/test_conversation.py`

- [ ] **Step 1: make_controller 보강 + 실패 테스트 추가** — `tests/test_conversation.py`

(a) `make_controller` 의 `deps` dict 에 추가(전이 시 기록):
```python
        persist_mode=lambda m: spans.setdefault("persist", []).append(m),
```
(`spans` 는 make_controller 안에 이미 있는 dict.)

(b) 파일 끝에 테스트 추가:
```python
def test_persist_mode_idle_and_translate():
    async def run():
        c = make_controller()
        await c._set_idle()
        assert "idle" in c.spans.get("persist", [])
        await c.start_translate("en")
        assert c.spans["persist"][-1] == "translate"
    asyncio.run(run())


def test_persist_mode_meeting_on_begin():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)
        await c.start_meeting()
        assert "meeting" in c.spans.get("persist", [])
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'persist_mode'`

- [ ] **Step 3: 구현** — `conversation.py`

(a) 생성자: 시그니처에 `persist_mode=lambda m: None,` 추가(예: `clock=time.monotonic` 근처), 본문에 `self.persist_mode = persist_mode` 저장(`self.web_pub = web_pub` 근처).

(b) `_set_idle`: `self.phase = None` 다음 줄에 삽입:
```python
        self.persist_mode("idle")
```

(c) `_to_listening`: `self.phase = Phase.LISTENING` 다음 줄에 삽입:
```python
        self.persist_mode("idle")
```

(d) `start_translate`: `self.mode = Mode.TRANSLATE` 다음 줄에 삽입:
```python
        self.persist_mode("translate")
```

(e) `_begin_meeting`: `self.meeting_phase = MeetingPhase.LIVE` 다음 줄에 삽입:
```python
        self.persist_mode("meeting")
```

- [ ] **Step 4: 통과 확인 + 전체**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과(실패 0).

- [ ] **Step 5: 커밋**
```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): persist_mode — 전이별 마지막 모드 저장"
```

---

### Task 3: main.py — persist_mode 배선 + 부팅 복구

**Files:** Modify `main.py`. READ main.py 먼저(복구 지점·ctor 위치 확인).

- [ ] **Step 1: import 추가** — 상단 import 블록에 `import runtime_state` 추가.

- [ ] **Step 2: 컨트롤러 생성자에 persist_mode 주입** — `controller = ConversationController(...)` 호출의 `hands_free_timeout_s=config.HANDS_FREE_TIMEOUT_S,` 줄 뒤에 추가:
```python
        persist_mode=runtime_state.save_mode,
```

- [ ] **Step 3: 초기 idle 을 복구 분기로 교체** — 현재 `await controller._set_idle()`(약 351) 한 줄을 다음으로 교체:
```python
    _restore = runtime_state.load_mode()
    if _restore == "meeting":
        console.log("🎤 이전 회의 모드를 복구합니다.")
        await controller.start_meeting()
    elif _restore == "translate":
        console.log("🌐 이전 번역 모드를 복구합니다.")
        await controller.start_translate(None)
    else:
        await controller._set_idle()
```
(load_mode 를 먼저 읽으므로 `_set_idle` 의 persist("idle") 가 저장값을 덮지 않는다. web_pub 은 이미 연결돼 navigate 가 웹에 도달.)

- [ ] **Step 4: 검증**
```bash
.venv/bin/python -c "import main, runtime_state; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 5: 커밋**
```bash
git add main.py
git commit -m "wire(main): persist_mode 배선 + 부팅 시 마지막 모드 복구"
```

---

## F3+F4 — DO

### Task 4: meeting_do.ts — currentView 동기화 + publisher 부재 통지

**Files:** Modify `jarvis-web/src/meeting_do.ts`. 검증: `cd jarvis-web && npm run typecheck`.

- [ ] **Step 1: 필드 추가** — 클래스 필드 영역(`private lastMicSource: string | null = null;` 근처)에 추가:
```typescript
  private currentView: string | null = null;
```

- [ ] **Step 2: navigate 에서 currentView 갱신** — `handlePublisherMessage` 의 navigate 케이스를 교체:
```typescript
    if (msg.kind === "navigate") {
      this.currentView = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));
      return;
    }
```

- [ ] **Step 3: attachViewer(owner) 에서 currentView·publisher 부재 동기화** — owner replay 블록(`if (role === "owner") { ... lastMicSource ... }`) 의 lastMicSource 전송 다음, 블록 닫기 전에 추가:
```typescript
      if (this.currentView === "meeting") {
        this.safeSend(ws, this.buildEvent({ kind: "navigate", text: "meeting" }));
      }
      if (!this.publisher) {
        this.safeSend(ws, this.buildEvent({ kind: "publisher_disconnected" }));
      }
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: 에러 없음(exit 0).

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(do): currentView 재접속 동기화 + 접속 시 publisher 부재 통지"
```

---

## F1+F3 — 웹

### Task 5: app.html — 로딩 오버레이 + serverUp 표시·음성비활성

**Files:** Modify `jarvis-web/src/static/app.html`. 검증: JS 구문검사 + `npm run typecheck`.

- [ ] **Step 1: CSS 추가** — `<style>` 안(예: `#login button { ... }` 다음)에 추가:
```css
  #meeting-loading { position: fixed; inset: 0; background: var(--bg); z-index: 60;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; }
  #meeting-loading .spinner { width: 42px; height: 42px; border: 4px solid #8884;
    border-top-color: var(--accent); border-radius: 50%; animation: spin 0.9s linear infinite; }
  #meeting-loading .lbl { color: var(--muted); font-size: 15px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #voice-toggle:disabled { opacity: 0.45; cursor: not-allowed; animation: none; }
```

- [ ] **Step 2: HTML 추가** — `#login` div 다음(또는 `#settings-modal` 근처)에 추가:
```html
  <div id="meeting-loading" class="hidden">
    <div class="spinner"></div>
    <div class="lbl">회의 준비 중…</div>
  </div>
```

- [ ] **Step 3: 로딩 오버레이 제어 + menu-meet 연동** — `$("menu-meet").addEventListener(...)` 를 교체하고, 헬퍼를 그 위에 추가:
```javascript
  let meetingLoadTimer = null;
  function showMeetingLoading() {
    $("meeting-loading").classList.remove("hidden");
    clearTimeout(meetingLoadTimer);
    meetingLoadTimer = setTimeout(() => {
      hideMeetingLoading();
      alert("회의 시작에 실패했어요. 다시 시도해주세요.");
    }, 10000);
  }
  function hideMeetingLoading() {
    clearTimeout(meetingLoadTimer); meetingLoadTimer = null;
    $("meeting-loading").classList.add("hidden");
  }
  $("menu-meet").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    showMeetingLoading();
    sendControl({ kind: "meeting_start" });
    $("plus-menu").classList.add("hidden");
  });
```

- [ ] **Step 4: navigate 에서 로딩 숨김** — `handle()` 의 navigate 케이스를 교체:
```javascript
      case "navigate":
        showView(ev.text); mic.apply();
        if (ev.text === "meeting") hideMeetingLoading();
        return;
```

- [ ] **Step 5: serverUp 추적 + setServerUp** — `let voiceOn = false;` 근처에 추가:
```javascript
  let serverUp = true;
  function setServerUp(up) {
    serverUp = up;
    $("voice-toggle").disabled = !up;
    if (up) {
      $("conn").textContent = "● live";
      $("conn").classList.remove("bad"); $("conn").classList.add("ok");
    } else {
      $("conn").textContent = "⚠ 서버 꺼짐";
      $("conn").classList.remove("ok"); $("conn").classList.add("bad");
      if (voiceOn) { voiceOn = false; $("voice-toggle").classList.remove("active"); mic.apply(); }
    }
  }
```

- [ ] **Step 6: handle() 의 hello / publisher_disconnected / end 에 serverUp 반영**

`case "hello":` 교체:
```javascript
      case "hello": applyMeta(ev.meta); setServerUp(true); return;
```
`case "publisher_disconnected":` 블록 교체(기존 #conn 직접 갱신 제거):
```javascript
      case "publisher_disconnected": {
        setServerUp(false);
        return;
      }
```
`case "end":` 블록에서 기존 `$("conn")...` 두 줄을 `setServerUp(false);` 로 교체(— 회의 종료 카드 생성은 유지):
```javascript
      case "end": {
        const card = newCard();
        card.innerHTML = `<div class="info">— 회의 종료 —</div>`;
        setServerUp(false);
        break;
      }
```

- [ ] **Step 7: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
```
Expected: `JS syntax OK`, typecheck 0.

- [ ] **Step 8: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/app.html
git commit -m "feat(web): 미팅 로딩 오버레이 + 서버다운 표시·음성버튼 비활성"
```

---

## Task 6: 최종 검증

**Files:** (변경 없음)

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, runtime_state, conversation; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){bad=1;console.error(e.message);}}if(bad)process.exit(1);console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포/재시작 후)**
- +메뉴 미팅 → 로딩 오버레이 → 회의 진입 시 사라짐(F1). (실패 시 10초 후 안내)
- 회의 중 jarvis 재시작 → 웹 우상단 "⚠ 서버 꺼짐"·음성버튼 비활성 → jarvis 복귀 시 회의 자동 복구·"● live"(F2/F3).
- 회의 중 웹 재배포(reload) → 재접속 시 회의 화면 복원·자막 replay(F4).

---

## 비고
- 저장값 없으면 idle(현 동작 보존). 복구 실패는 `_begin_meeting` try/except 가 idle 폴백.
- 배포: jarvis 재시작 + 웹 `cd jarvis-web && npx wrangler deploy`. origin push 직접.
- `.jarvis_state.json` 은 gitignore.
