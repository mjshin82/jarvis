# 설정 기능 (미팅 번역/STT 백엔드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 설정 팝업에서 미팅 번역(딥시크/로컬)·STT(Deepgram/로컬) 백엔드를 고르면 jarvis 가 setting.yaml 로 저장·적용하고, `/reload-settings` 로 재로드한다.

**Architecture:** `settings.py` 가 setting.yaml 을 소유(load/apply/save). 제어 채널을 페이로드(전체 dict)로 확장해 web 이 get/apply_settings 를 보냄. jarvis 는 `settings` 스냅샷을 owner 뷰어에 발행. live_translate 가 회의 시작 시 settings 로 백엔드 결정.

**Tech Stack:** Python(pyyaml, pytest) · Cloudflare Worker(TS) · 바닐라 JS.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`. typecheck: `cd jarvis-web && npm run typecheck`.

---

## Task 1: settings.py + 의존성 + 테스트

**Files:** Create `settings.py`, `tests/test_settings.py`; Modify `requirements.txt`, `.gitignore`

- [ ] **Step 1: 실패 테스트** — `tests/test_settings.py`:
```python
# tests/test_settings.py
import os
import settings


def test_defaults():
    assert settings.DEFAULTS["translate_backend"] == "deepseek"
    assert settings.DEFAULTS["stt_backend"] == "deepgram"


def test_load_creates_file_with_defaults(tmp_path):
    p = str(tmp_path / "setting.yaml")
    cur = settings.load(p)
    assert cur == settings.DEFAULTS
    assert os.path.exists(p)


def test_apply_filters_invalid_and_persists(tmp_path):
    p = str(tmp_path / "setting.yaml")
    settings.load(p)
    cur = settings.apply({"translate_backend": "local", "stt_backend": "bogus"}, p)
    assert cur["translate_backend"] == "local"
    assert cur["stt_backend"] == "deepgram"   # 무효값 무시 → 기본 유지
    settings.load(p)                           # 재로드해도 저장됨
    assert settings.get("translate_backend") == "local"
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_settings.py -v` → FAIL (ModuleNotFoundError: settings).

- [ ] **Step 3: 구현** — `settings.py`:
```python
# settings.py
"""사용자 설정(미팅 번역/STT 백엔드) — setting.yaml 영속.
웹 설정 팝업이 편집(apply), 콘솔은 /reload-settings 로 재로드(load)만."""
import os
import yaml

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setting.yaml")

DEFAULTS = {
    "translate_backend": "deepseek",   # deepseek | local
    "stt_backend": "deepgram",         # deepgram | local
}
ALLOWED = {
    "translate_backend": {"deepseek", "local"},
    "stt_backend": {"deepgram", "local"},
}

_current = dict(DEFAULTS)


def current() -> dict:
    return dict(_current)


def get(key: str):
    return _current.get(key, DEFAULTS.get(key))


def _valid(updates: dict) -> dict:
    out = {}
    for k, v in (updates or {}).items():
        if k in ALLOWED and v in ALLOWED[k]:
            out[k] = v
    return out


def save(path: str = None) -> None:
    with open(path or PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(_current, f, allow_unicode=True, sort_keys=True)


def load(path: str = None) -> dict:
    """파일 읽어 _current 갱신(기본값 위 병합). 없으면 기본값으로 생성."""
    global _current
    p = path or PATH
    _current = dict(DEFAULTS)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _current.update(_valid(data))
        else:
            save(p)
    except Exception:
        _current = dict(DEFAULTS)
    return current()


def apply(updates: dict, path: str = None) -> dict:
    """유효한 키/값만 갱신 후 저장."""
    _current.update(_valid(updates))
    save(path)
    return current()
```

- [ ] **Step 4: 의존성·gitignore** — `requirements.txt` 끝에 `pyyaml` 한 줄 추가(이미 있으면 생략). `.gitignore` 끝에 `setting.yaml` 추가.

- [ ] **Step 5: 통과 확인** — `.venv/bin/python -m pytest tests/test_settings.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 6: 커밋**
```bash
git add settings.py tests/test_settings.py requirements.txt .gitignore
git commit -m "feat: settings.py — setting.yaml 로드/적용/저장 + 테스트"
```

---

## Task 2: control_receiver — on_command(msg) 페이로드

**Files:** Modify `control_receiver.py`, `tests/test_control_receiver.py`

- [ ] **Step 1: 테스트 갱신** — `tests/test_control_receiver.py` 전체를 교체:
```python
# tests/test_control_receiver.py
import asyncio
from control_receiver import ControlReceiver


def _rx(calls):
    async def on_command(msg):
        calls.append(msg)
    return ControlReceiver("ws://x", "tok", on_command=on_command,
                           on_log=lambda *a: None, key="k")


def test_dispatch_passes_full_msg():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"meeting_stop"}'))
    assert calls == [{"kind": "meeting_stop"}]


def test_dispatch_with_payload():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"apply_settings","value":{"translate_backend":"local"}}'))
    assert calls == [{"kind": "apply_settings", "value": {"translate_backend": "local"}}]


def test_non_commands_ignored():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"no_receiver"}'))   # 로그만
    asyncio.run(rx._handle_message("not json at all"))
    asyncio.run(rx._handle_message('{"no":"kind"}'))
    assert calls == []
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_control_receiver.py -v` → `test_dispatch_passes_full_msg`/`test_dispatch_with_payload` FAIL (현재 kind 문자열만 전달).

- [ ] **Step 3: 구현** — `control_receiver.py` `_handle_message` 끝부분. 현재:
```python
        kind = msg.get("kind")
        if kind == "no_receiver":
            self.on_log("[control] relay: 수신자 없음 통지")
        elif kind:
            await self.on_command(kind)   # meeting_stop·listen_start·listen_stop 등 포워딩
```
교체:
```python
        kind = msg.get("kind")
        if kind == "no_receiver":
            self.on_log("[control] relay: 수신자 없음 통지")
        elif kind:
            await self.on_command(msg)   # 전체 dict 전달(kind + value 등 페이로드)
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_control_receiver.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 5: 커밋**
```bash
git add control_receiver.py tests/test_control_receiver.py
git commit -m "feat: ControlReceiver on_command 에 전체 dict 전달(페이로드)"
```

---

## Task 3: main.py — settings 로드/스냅샷 + _on_remote_command(msg)

**Files:** Modify `main.py`

- [ ] **Step 1: import** — main.py 상단 import 블록(`import coach` 근처)에 추가:
```python
import json
import settings
```
(`import json` 이 이미 있으면 그 줄은 생략.)

- [ ] **Step 2: 시작 시 settings 로드** — 현재(56-57행):
```python
    web_pub = None
    web_speaking_until = 0.0   # 웹으로 TTS 재생 추정 종료 시각(에코 게이트)
```
교체(앞에 load 추가):
```python
    settings.load()            # setting.yaml 로드(없으면 기본값으로 생성)
    web_pub = None
    web_speaking_until = 0.0   # 웹으로 TTS 재생 추정 종료 시각(에코 게이트)
```

- [ ] **Step 3: 시작 시 스냅샷 발행** — `await web_pub.connect()` 다음 줄에 추가. 현재:
```python
        await web_pub.connect()
        home_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
```
교체:
```python
        await web_pub.connect()
        web_pub.emit("settings", json.dumps(settings.current()))   # 초기 스냅샷(replay 로 늦은 owner 도 받음)
        home_base = config.RELAY_URL.replace("wss://", "https://").replace("ws://", "http://")
```

- [ ] **Step 4: _on_remote_command 를 msg 기반으로 + 설정 명령** — 현재:
```python
        async def _on_remote_command(kind):
            nonlocal hands_free, response, watchdog, stop_after_response
            if kind == "meeting_stop":
                await stop_meeting()
```
교체(시그니처 msg + kind 추출):
```python
        async def _on_remote_command(msg):
            nonlocal hands_free, response, watchdog, stop_after_response
            kind = msg.get("kind")
            if kind == "meeting_stop":
                await stop_meeting()
```
그리고 같은 함수의 listen_stop 분기 끝(아래) 다음에 설정 분기 추가. 현재 listen_stop 끝:
```python
            elif kind == "listen_stop":
                hands_free = False
                if response is not None and not response.done():
                    stop_after_response = True   # TTS 응답 진행 중 — 끊지 말고 끝나면 idle
                else:
                    if watchdog is not None and not watchdog.done():
                        watchdog.cancel()
                    watchdog = None
                    idle()
```
다음에 추가:
```python
            elif kind == "get_settings":
                if web_pub is not None:
                    web_pub.emit("settings", json.dumps(settings.current()))
            elif kind == "apply_settings":
                settings.apply(msg.get("value") or {})
                console.log(f"⚙️ 설정 변경: {settings.current()}")
                if web_pub is not None:
                    web_pub.emit("settings", json.dumps(settings.current()))
```

- [ ] **Step 5: cmd_ctx 에 web_pub** — cmd_ctx 딕셔너리의 `"mic_router": (mic.router if config.REMOTE_MIC_ENABLED else None),` 줄 다음에 추가:
```python
        "web_pub": web_pub,
```

- [ ] **Step 6: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `grep -c 'get_settings\|apply_settings\|settings.load()' main.py` → `3` 이상
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 7: 커밋**
```bash
git add main.py
git commit -m "feat: main — settings 로드/스냅샷 + get/apply_settings 제어 + cmd_ctx web_pub"
```

---

## Task 4: live_translate 적용 + /reload-settings

**Files:** Modify `live_translate.py`, `commands.py`

- [ ] **Step 1: live_translate import** — `import wordbook` 다음 줄에 추가:
```python
import settings
```

- [ ] **Step 2: 번역 백엔드 settings 기준** — 현재 `_setup_translator` 의:
```python
        use_remote = (config.MEET_REMOTE_ENABLED
                      and config.DEEPSEEK_API_KEY
                      and config.DEEPSEEK_API_KEY != "sk-your-key-here")
```
교체:
```python
        use_remote = (settings.get("translate_backend") == "deepseek"
                      and config.DEEPSEEK_API_KEY
                      and config.DEEPSEEK_API_KEY != "sk-your-key-here")
```

- [ ] **Step 3: STT 백엔드 분기점** — 현재:
```python
        # RealtimeSTT 는 장치를 직접 잡지 않는다 — jarvis 가 feed_block 으로 먹인다.
        rec_kwargs["use_microphone"] = False

        self.recorder = AudioToTextRecorder(**rec_kwargs)
```
교체(deepgram 은 다음 작업 — 현재 로컬 폴백 + 로그):
```python
        # RealtimeSTT 는 장치를 직접 잡지 않는다 — jarvis 가 feed_block 으로 먹인다.
        rec_kwargs["use_microphone"] = False

        if settings.get("stt_backend") == "deepgram":
            self.log("⚙️ Deepgram STT 는 다음 작업 — 현재 로컬 STT 사용")
        self.recorder = AudioToTextRecorder(**rec_kwargs)
```

- [ ] **Step 4: /reload-settings 명령** — `commands.py` 파일 끝에 추가:
```python


@command("reload-settings", help="setting.yaml 재로드")
async def _reload_settings(args: str, ctx: dict):
    import json
    import settings
    settings.load()
    ctx["log"](f"⚙️ setting.yaml 재로드: {settings.current()}")
    web_pub = ctx.get("web_pub")
    if web_pub is not None:
        web_pub.emit("settings", json.dumps(settings.current()))
```

- [ ] **Step 5: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('live_translate.py').read()); ast.parse(open('commands.py').read()); import live_translate, commands; print('ok')"` → `ok`
Run: `grep -c 'translate_backend\|stt_backend' live_translate.py` → `2` 이상; `grep -c 'reload-settings' commands.py` → `1`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 6: 커밋**
```bash
git add live_translate.py commands.py
git commit -m "feat: live_translate 번역/STT 백엔드를 settings 기준으로 + /reload-settings"
```

---

## Task 5: 웹 — settings kind + 설정 메뉴/모달

**Files:** Modify `jarvis-web/src/types.ts`, `jarvis-web/src/static/app.html`

- [ ] **Step 1: types.ts** — `EventKind` 끝 `| "viewers";` 다음에 추가:
```ts
  | "settings";        // worker → owner viewer: 현재 설정 스냅샷(JSON in text)
```

- [ ] **Step 2: 메뉴 항목** — `#plus-menu` 현재:
```html
  <div id="plus-menu" class="hidden">
    <button id="menu-meet">🎤 미팅모드</button>
  </div>
```
교체:
```html
  <div id="plus-menu" class="hidden">
    <button id="menu-meet">🎤 미팅모드</button>
    <button id="menu-settings">⚙️ 설정</button>
  </div>
```

- [ ] **Step 3: 모달 DOM** — `#plus-menu` 닫는 `</div>` 다음(위 블록 바로 아래)에 추가:
```html
  <div id="settings-modal" class="hidden">
    <div class="sheet">
      <div class="sheet-head"><b>설정</b><button id="settings-close">✕</button></div>
      <div class="sheet-row">
        <div class="sheet-label">미팅 번역</div>
        <label><input type="radio" name="set-translate" value="deepseek"> 딥시크</label>
        <label><input type="radio" name="set-translate" value="local"> 로컬</label>
      </div>
      <div class="sheet-row">
        <div class="sheet-label">미팅 STT</div>
        <label><input type="radio" name="set-stt" value="deepgram"> Deepgram</label>
        <label><input type="radio" name="set-stt" value="local"> 로컬</label>
      </div>
    </div>
  </div>
```

- [ ] **Step 4: 모달 CSS** — `<style>` 의 `#plus-menu button { ... }` 줄 다음에 추가:
```css
  #settings-modal { position: fixed; inset: 0; background: #0008; z-index: 25;
    display: flex; align-items: center; justify-content: center; }
  #settings-modal.hidden { display: none; }
  #settings-modal .sheet { background: var(--bg); color: var(--fg); border: 1px solid var(--border);
    border-radius: 14px; padding: 16px; width: min(92vw, 360px); }
  #settings-modal .sheet-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  #settings-modal .sheet-head button { background: transparent; color: var(--fg); padding: 4px 8px; }
  #settings-modal .sheet-row { margin: 12px 0; }
  #settings-modal .sheet-label { font-size: 13px; color: var(--muted); margin-bottom: 6px; }
  #settings-modal label { margin-right: 16px; font-size: 15px; cursor: pointer; }
```

- [ ] **Step 5: settings 이벤트 핸들러** — `handle(ev)` switch 의 `case "mic_source":` 분기 **앞**에 추가:
```js
      case "settings":
        try { fillSettings(JSON.parse(ev.text || "{}")); } catch {}
        return;
```

- [ ] **Step 6: 설정 JS** — 기존 menu-meet 핸들러(아래) 다음에 추가:
```js
  $("menu-meet").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    sendControl({ kind: "meeting_start" });
    $("plus-menu").classList.add("hidden");
  });
```
삽입:
```js
  // ---- 설정 모달 ----
  function fillSettings(s) {
    document.querySelectorAll('input[name="set-translate"]').forEach((r) => { r.checked = (r.value === s.translate_backend); });
    document.querySelectorAll('input[name="set-stt"]').forEach((r) => { r.checked = (r.value === s.stt_backend); });
  }
  function curSettings() {
    const t = document.querySelector('input[name="set-translate"]:checked');
    const s = document.querySelector('input[name="set-stt"]:checked');
    return { translate_backend: t ? t.value : "deepseek", stt_backend: s ? s.value : "deepgram" };
  }
  $("menu-settings").addEventListener("click", () => {
    $("plus-menu").classList.add("hidden");
    if (!getPw()) { showLogin(); return; }
    $("settings-modal").classList.remove("hidden");
    sendControl({ kind: "get_settings" });
  });
  $("settings-close").addEventListener("click", () => $("settings-modal").classList.add("hidden"));
  $("settings-modal").addEventListener("click", (e) => { if (e.target === $("settings-modal")) $("settings-modal").classList.add("hidden"); });
  document.querySelectorAll('#settings-modal input[type="radio"]').forEach((r) =>
    r.addEventListener("change", () => sendControl({ kind: "apply_settings", value: curSettings() }))
  );
```

- [ ] **Step 7: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/appset.js && node --check /tmp/appset.js && echo "JS OK"` → `JS OK`
`cd jarvis-web && npm run typecheck` → 오류 없음
`cd /Users/oracle/Documents/concode/jarvis && grep -c 'menu-settings\|settings-modal\|apply_settings\|get_settings' jarvis-web/src/static/app.html` → `6` 이상
```bash
git add jarvis-web/src/types.ts jarvis-web/src/static/app.html
git commit -m "feat(jarvis-web): + 메뉴 설정 + 설정 모달(번역/STT 백엔드)"
```

---

## Task 6: 검증 + 배포

**Files:** (없음)

- [ ] **Step 1: 전체 검증** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed; `cd jarvis-web && npm run typecheck` → 오류 없음.

- [ ] **Step 2: best-effort 통합(회귀)** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s) → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 기존 줄 OK → `pkill -f "wrangler dev"`. 안 뜨면 스킵.

- [ ] **Step 3: 배포** — `cd jarvis-web && npm run deploy`. 수동 E2E: 웹 `+`→⚙️ 설정 → 모달에 현재값 표시(딥시크/Deepgram) → "로컬" 선택 → `setting.yaml` 에 `translate_backend: local` 저장(jarvis 콘솔 "⚙️ 설정 변경" 로그) → `/meet` 시 번역 라벨 "local (...)". 콘솔 `/reload-settings` → 재로드 로그. (jarvis 재시작 필요.)

---

## Self-Review 결과

**Spec coverage:**
- settings.py(load/apply/save) + setting.yaml + gitignore + pyyaml → Task 1 ✓
- 제어 페이로드(on_command msg) → Task 2 ✓
- main settings 로드/스냅샷 + get/apply_settings + cmd_ctx web_pub → Task 3 ✓
- live_translate 번역/STT 백엔드 settings 기준(STT deepgram→로컬 폴백 로그) → Task 4 ✓
- /reload-settings(편집 없음) → Task 4 ✓
- types settings kind + 웹 메뉴/모달 → Task 5 ✓
- 검증·배포 → Task 6 ✓
- 비범위(Deepgram 연동, 실시간 적용) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `translate_backend`/`stt_backend` 가 settings.DEFAULTS·ALLOWED ↔ live_translate `settings.get(...)` ↔ web radios value/curSettings ↔ apply_settings value ↔ setting.yaml 키 일관. `get_settings`/`apply_settings`/`settings`(이벤트) kind 가 web sendControl ↔ main _on_remote_command ↔ web_pub.emit("settings") ↔ types EventKind ↔ handle("settings"). `on_command(msg)` dict ↔ control_receiver ↔ main. cmd_ctx `web_pub` ↔ /reload-settings.

**핵심 위험:** (1) on_command 시그니처 변경(kind→msg) — 모든 분기 `msg.get("kind")` 로 통일(Task3), 테스트 갱신(Task2). (2) settings 모듈 전역 _current — load() 가 매번 리셋, 테스트 tmp 경로 격리. (3) STT deepgram 선택해도 이번엔 로컬 동작(로그) — 회의 안 깨짐. (4) settings 이벤트는 PUBLIC_KINDS 미포함 → 공개 뷰어 제외(SP4). (5) 설정은 다음 회의부터(세션 시작 시 settings.get) — 의도.
