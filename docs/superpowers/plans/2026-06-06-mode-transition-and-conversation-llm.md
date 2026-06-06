# 모드 전환 효과 + 일반 대화 LLM 선택 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 모드 전환에 토스트+페이드(G)를 추가하고, 일반 대화 LLM 을 딥시크/로컬에서 라이브 선택(H)하게 한다.

**Architecture:** H 는 `LLM.set_backend()` 로 client/model/extra 를 재구성(기존 사용처 무변경), 설정 변경/부팅/리로드 시 호출. G 는 `showView` 한 곳에서 페이드 애니메이션 + 토스트 배너를 띄운다.

**Tech Stack:** Python 3.11 + pytest(monkeypatch); TS Worker(`npm run typecheck`); 웹 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-06-mode-transition-and-conversation-llm-design.md`

---

## H — 일반 대화 LLM 선택

### Task 1: settings 키 + `LLM.set_backend` + 테스트

**Files:** Modify `settings.py`, `llm.py`; Create `tests/test_llm_backend.py`

- [ ] **Step 1: settings 키 추가** — `settings.py`

상단에 `import config` 추가(다른 import 근처). `ALLOWED` 에
`"conversation_llm_backend": {"deepseek", "local"},` 추가. `DEFAULTS` 에
(env 의도 보존을 위해 파생값으로) 추가:
```python
    "conversation_llm_backend": "local" if config.LLM_BACKEND == "local" else "deepseek",
```

- [ ] **Step 2: 실패 테스트 작성** — `tests/test_llm_backend.py`

```python
import config
from llm import LLM


def test_set_backend_local(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "remote")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-real")
    llm = LLM()
    llm.set_backend("local")
    assert llm.backend == "local"
    assert llm.model == config.LOCAL_MODEL
    assert llm.extra.get("keep_alive") == config.OLLAMA_KEEP_ALIVE


def test_set_backend_deepseek_with_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "remote")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-real")
    llm = LLM()
    llm.set_backend("deepseek")
    assert llm.backend == "remote"
    assert llm.model == config.DEEPSEEK_MODEL
    assert llm.extra == {}


def test_deepseek_falls_back_to_local_without_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "remote")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    llm = LLM()
    llm.set_backend("deepseek")
    assert llm.backend == "local"


def test_mock_ignores_set_backend(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "mock")
    llm = LLM()
    llm.set_backend("deepseek")
    assert llm.client is None and llm.backend == "mock"
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_llm_backend.py -q`
Expected: FAIL — `AttributeError: 'LLM' object has no attribute 'set_backend'` (또는 mock 분기 차이)

- [ ] **Step 4: 구현** — `llm.py`

(a) 상단 import 에 `import settings` 추가.

(b) `__init__` 의 backend 분기(`if self.backend == "mock": ... elif remote ... elif local ... else raise`)와 그 아래 `self.extra = ...` 줄을 다음으로 교체:
```python
    def __init__(self):
        self._mock = (config.LLM_BACKEND == "mock")
        self.backend = "mock"
        self.client = None
        self.model = None
        self.extra = {}
        if self._mock:
            print(f"[llm] ⚠️  MOCK 모드: '{config.MOCK_MESSAGE}' 로만 응답합니다.")
        else:
            self.set_backend(settings.get("conversation_llm_backend"))
```
(그 아래 `self.tools = []` 부터의 도구 구성 로직은 그대로 둔다 — `self.client` 가 set_backend 로 이미 설정됨.)

(c) `set_backend` 메서드 추가(예: `__init__` 다음):
```python
    def set_backend(self, backend: str) -> None:
        """대화 LLM 백엔드 전환(deepseek=remote / local=ollama). mock 이면 무시.
        전제 미충족(예: deepseek 키 없음) 시 사용 가능한 쪽으로 폴백."""
        if self._mock:
            return
        want = "remote" if backend == "deepseek" else "local"
        has_remote = bool(config.DEEPSEEK_API_KEY and config.DEEPSEEK_API_KEY != "sk-your-key-here")
        if want == "remote" and not has_remote:
            want = "local"
        if want == "remote":
            self.client = AsyncOpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
            self.model = config.DEEPSEEK_MODEL
            self.extra = {}
            self.backend = "remote"
        else:
            self.client = AsyncOpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL)
            self.model = config.LOCAL_MODEL
            self.extra = {"keep_alive": config.OLLAMA_KEEP_ALIVE}
            self.backend = "local"
        print(f"[llm] 대화 LLM 백엔드: {self.backend} ({self.model})")
```

- [ ] **Step 5: 통과 확인 + 전체**

Run: `.venv/bin/python -m pytest tests/test_llm_backend.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과(실패 0).

- [ ] **Step 6: 커밋**
```bash
git add settings.py llm.py tests/test_llm_backend.py
git commit -m "feat(llm): set_backend(딥시크/로컬) 라이브 전환 + conversation_llm_backend 설정"
```

---

### Task 2: 설정 변경/리로드 시 LLM 백엔드 적용 (main.py + commands.py)

**Files:** Modify `main.py`, `commands.py`

- [ ] **Step 1: main.py — apply_settings 시 적용**

`_on_remote_command` 의 `apply_settings` 분기에서 `settings.apply(msg.get("value") or {})` 다음 줄에 추가:
```python
                llm.set_backend(settings.get("conversation_llm_backend"))
```
(`llm` 은 main 스코프에 존재.)

- [ ] **Step 2: commands.py — /reload-settings 시 적용**

`_reload_settings` 핸들러에서 `settings.load()` 다음에 추가:
```python
    _llm = ctx.get("llm")
    if _llm is not None:
        _llm.set_backend(settings.get("conversation_llm_backend"))
```
(`cmd_ctx["llm"]` 이미 존재.)

- [ ] **Step 2.5: 검증**
```bash
.venv/bin/python -c "import main, commands; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 3: 커밋**
```bash
git add main.py commands.py
git commit -m "wire(llm): apply_settings·/reload-settings 시 대화 LLM 백엔드 적용"
```

---

## G + H 웹

### Task 3: app.html — 모드 전환 토스트/페이드(G) + 일반 대화 LLM 라디오(H)

**Files:** Modify `jarvis-web/src/static/app.html`. 검증: JS 구문검사 + `npm run typecheck`.

- [ ] **Step 1: CSS 추가** — `<style>` 안(예: `#meeting-loading .lbl { ... }` 근처)에 추가:
```css
  #mode-toast { position: fixed; top: 64px; left: 50%; transform: translateX(-50%) translateY(-8px);
    background: var(--accent); color: #fff; padding: 8px 16px; border-radius: 999px; font-size: 14px;
    z-index: 40; opacity: 0; pointer-events: none; transition: opacity .25s, transform .25s; box-shadow: 0 2px 10px #0005; }
  #mode-toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  @keyframes viewfade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
```

- [ ] **Step 2: HTML 추가** — `#meeting-loading` div 다음에 추가:
```html
  <div id="mode-toast"></div>
```

- [ ] **Step 3: showView 에 페이드 + 토스트 + 헬퍼** — `function showView(v) { ... }` 를 교체하고 그 위에 헬퍼 추가:
```javascript
  let modeToastTimer = null;
  function showModeToast(text) {
    const t = $("mode-toast");
    t.textContent = text; t.classList.add("show");
    clearTimeout(modeToastTimer);
    modeToastTimer = setTimeout(() => t.classList.remove("show"), 1500);
  }
  function showView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    if (document.body.dataset.view === nv) return;
    setView(nv);   // URL 은 /{name} 유지 — /meeting 은 공개 뷰어
    const el = nv === "meeting" ? $("meeting-view") : $("home-view");
    el.style.animation = "none"; void el.offsetWidth; el.style.animation = "viewfade .25s ease";
    showModeToast(nv === "meeting" ? "🎤 회의 모드" : "💬 일반 모드");
  }
```

- [ ] **Step 4: 설정 모달에 "일반 대화 LLM" 행** — "일반 대화 STT"(`set-conv-stt`) sheet-row 다음에 추가:
```html
      <div class="sheet-row">
        <div class="sheet-label">일반 대화 LLM</div>
        <label><input type="radio" name="set-conv-llm" value="deepseek"> 딥시크</label>
        <label><input type="radio" name="set-conv-llm" value="local"> 로컬</label>
      </div>
```

- [ ] **Step 5: fillSettings / curSettings 갱신**

`fillSettings(s)` 의 set-conv-stt 줄 다음에 추가:
```javascript
    document.querySelectorAll('input[name="set-conv-llm"]').forEach((r) => { r.checked = (r.value === s.conversation_llm_backend); });
```
`curSettings()` 를 교체:
```javascript
  function curSettings() {
    const t = document.querySelector('input[name="set-translate"]:checked');
    const s = document.querySelector('input[name="set-stt"]:checked');
    const c = document.querySelector('input[name="set-conv-stt"]:checked');
    const l = document.querySelector('input[name="set-conv-llm"]:checked');
    return { translate_backend: t ? t.value : "deepseek", stt_backend: s ? s.value : "gladia",
             conversation_stt_backend: c ? c.value : "local",
             conversation_llm_backend: l ? l.value : "deepseek" };
  }
```

- [ ] **Step 6: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
```
Expected: `JS syntax OK`, typecheck 0.

- [ ] **Step 7: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/app.html
git commit -m "feat(web): 모드 전환 토스트+페이드 + 일반 대화 LLM 설정 행"
```

---

## Task 4: 최종 검증

**Files:** (변경 없음)

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, llm, settings; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){bad=1;console.error(e.message);}}if(bad)process.exit(1);console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포/재시작 후)**
- 홈↔회의 전환 시 상단 토스트("🎤 회의 모드"/"💬 일반 모드") + 화면 페이드(G).
- 설정에서 "일반 대화 LLM = 로컬/딥시크" 변경 → 다음 발화가 해당 backend(jarvis 로그 "[llm] 대화 LLM 백엔드: …")(H).

---

## 비고
- H 기본값은 env(LLM_BACKEND)에서 파생 — 현 동작 보존. mock 모드는 설정 무시.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
