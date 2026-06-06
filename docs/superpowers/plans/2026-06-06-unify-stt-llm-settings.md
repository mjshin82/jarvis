# STT·LLM 설정 일원화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 미팅/일반으로 쪼개진 STT·LLM 설정 4개를 통합 `stt_backend`·`llm_backend` 2개로 합치고, 미팅·일반 양쪽이 같은 키를 읽게 한다.

**Architecture:** settings 의 키만 2개로 정리(+구버전 이주)하고, 각 읽기 지점(conversation_stt/live_translate/llm/main/commands)이 통합 키를 참조하도록 바꾼다. 웹 설정 모달은 4행→2행. backend 구현은 무변경.

**Tech Stack:** Python 3.11 + pytest(tmp_path); TS Worker(`npm run typecheck`); 웹 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-06-unify-stt-llm-settings-design.md`

---

## Task 1: settings.py 통합 키 + 이주 + 테스트

**Files:** Modify `settings.py`, `tests/test_settings.py`

- [ ] **Step 1: test_settings.py 전체 교체(실패 테스트)**

```python
# tests/test_settings.py
import os
import yaml
import settings


def test_defaults():
    assert settings.DEFAULTS["stt_backend"] == "gladia"
    assert settings.DEFAULTS["llm_backend"] in ("deepseek", "local")
    assert "translate_backend" not in settings.DEFAULTS
    assert "conversation_stt_backend" not in settings.DEFAULTS
    assert "conversation_llm_backend" not in settings.DEFAULTS


def test_load_creates_file_with_defaults(tmp_path):
    p = str(tmp_path / "setting.yaml")
    cur = settings.load(p)
    assert cur == settings.DEFAULTS
    assert os.path.exists(p)


def test_apply_filters_invalid_and_persists(tmp_path):
    p = str(tmp_path / "setting.yaml")
    settings.load(p)
    cur = settings.apply({"llm_backend": "local", "stt_backend": "bogus"}, p)
    assert cur["llm_backend"] == "local"
    assert cur["stt_backend"] == "gladia"   # 무효값 무시 → 기본 유지
    settings.load(p)                          # 재로드해도 저장됨
    assert settings.get("llm_backend") == "local"


def test_apply_ignores_legacy_keys(tmp_path):
    p = str(tmp_path / "setting.yaml")
    settings.load(p)
    cur = settings.apply({"translate_backend": "local", "conversation_stt_backend": "gladia"}, p)
    assert "translate_backend" not in cur
    assert "conversation_stt_backend" not in cur


def test_migrates_legacy_conversation_llm_key(tmp_path):
    p = str(tmp_path / "setting.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump({"conversation_llm_backend": "local", "stt_backend": "local"}, f)
    settings.load(p)
    assert settings.get("llm_backend") == "local"
    assert settings.get("stt_backend") == "local"


def test_migrates_translate_backend_when_no_conv_llm(tmp_path):
    p = str(tmp_path / "setting.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump({"translate_backend": "local"}, f)
    settings.load(p)
    assert settings.get("llm_backend") == "local"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_settings.py -q`
Expected: FAIL (구 DEFAULTS 에 translate_backend 존재 등)

- [ ] **Step 3: settings.py 구현**

(a) `DEFAULTS` 를 교체:
```python
DEFAULTS = {
    "stt_backend": "gladia",                                                  # gladia | local
    "llm_backend": "local" if config.LLM_BACKEND == "local" else "deepseek",  # deepseek | local
}
```
(b) `ALLOWED` 를 교체:
```python
ALLOWED = {
    "stt_backend": {"gladia", "local"},
    "llm_backend": {"deepseek", "local"},
}
```
(c) `load()` 에 구버전 이주 추가 — `_current.update(_valid(data))` 바로 위에:
```python
            # 구버전 키 이주: llm_backend 없으면 옛 키(conversation_llm/translate)에서 가져옴.
            if "llm_backend" not in data:
                old = data.get("conversation_llm_backend") or data.get("translate_backend")
                if old:
                    data["llm_backend"] = old
```
(`import config` 는 이미 상단에 있음. `_valid` 가 ALLOWED 밖 구 키를 자동 제거.)

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_settings.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**
```bash
git add settings.py tests/test_settings.py
git commit -m "feat(settings): stt_backend/llm_backend 2키로 통합 + 구버전 이주"
```

---

## Task 2: 읽기 지점 통합 키로 전환 (Python)

**Files:** Modify `conversation_stt.py`, `live_translate.py`, `llm.py`, `main.py`, `commands.py`

- [ ] **Step 1: conversation_stt.py**

`_backend()` 의 `return self._settings_get("conversation_stt_backend") or "local"` →
```python
        return self._settings_get("stt_backend") or "local"
```

- [ ] **Step 2: live_translate.py**

`_setup_translator()` 의 `use_remote = (settings.get("translate_backend") == "deepseek"` →
```python
        use_remote = (settings.get("llm_backend") == "deepseek"
```
(나머지 조건/들여쓰기 유지. STT 읽기 `settings.get("stt_backend")` 는 무변경.)

- [ ] **Step 3: llm.py**

`__init__` 의 `self.set_backend(settings.get("conversation_llm_backend"))` →
```python
            self.set_backend(settings.get("llm_backend"))
```

- [ ] **Step 4: main.py**

`_on_remote_command` apply_settings 의 `llm.set_backend(settings.get("conversation_llm_backend"))` →
```python
                llm.set_backend(settings.get("llm_backend"))
```

- [ ] **Step 5: commands.py**

`_reload_settings` 의 `_llm.set_backend(settings.get("conversation_llm_backend"))` →
```python
        _llm.set_backend(settings.get("llm_backend"))
```

- [ ] **Step 6: 잔존 확인 + import + 전체**

Run:
```bash
grep -rn "conversation_stt_backend\|conversation_llm_backend\|translate_backend" *.py | grep -v "test_" || echo "CLEAN"
.venv/bin/python -c "import main, settings, llm, conversation_stt, live_translate; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `CLEAN`(파이썬 소스에 구 키 없음), `import ok`, 전체 통과(실패 0).

- [ ] **Step 7: 커밋**
```bash
git add conversation_stt.py live_translate.py llm.py main.py commands.py
git commit -m "refactor: STT/LLM 읽기 지점을 통합 stt_backend/llm_backend 로"
```

---

## Task 3: 웹 설정 모달 2행 (app.html)

**Files:** Modify `jarvis-web/src/static/app.html`. 검증: JS 구문 + `npm run typecheck`.

- [ ] **Step 1: 설정 모달 4행 → 2행 교체**

현재 4개 sheet-row(미팅 번역 `set-translate` / 미팅 STT `set-stt` / 일반 대화 STT `set-conv-stt` / 일반 대화 LLM `set-conv-llm`)를 다음 2개로 교체:
```html
      <div class="sheet-row">
        <div class="sheet-label">STT</div>
        <label><input type="radio" name="set-stt" value="gladia"> Gladia</label>
        <label><input type="radio" name="set-stt" value="local"> 로컬</label>
      </div>
      <div class="sheet-row">
        <div class="sheet-label">LLM</div>
        <label><input type="radio" name="set-llm" value="deepseek"> 딥시크</label>
        <label><input type="radio" name="set-llm" value="local"> 로컬</label>
      </div>
```

- [ ] **Step 2: fillSettings 교체**

```javascript
  function fillSettings(s) {
    document.querySelectorAll('input[name="set-stt"]').forEach((r) => { r.checked = (r.value === s.stt_backend); });
    document.querySelectorAll('input[name="set-llm"]').forEach((r) => { r.checked = (r.value === s.llm_backend); });
  }
```

- [ ] **Step 3: curSettings 교체**

```javascript
  function curSettings() {
    const s = document.querySelector('input[name="set-stt"]:checked');
    const l = document.querySelector('input[name="set-llm"]:checked');
    return { stt_backend: s ? s.value : "gladia", llm_backend: l ? l.value : "deepseek" };
  }
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
grep -n "set-translate\|set-conv-stt\|set-conv-llm\|conversation_stt_backend\|conversation_llm_backend\|translate_backend" jarvis-web/src/static/app.html || echo "CLEAN"
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
```
Expected: `CLEAN`(구 라디오/키 없음), `JS syntax OK`, typecheck 0.

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/app.html
git commit -m "feat(web): 설정 모달 STT/LLM 2행으로 통합"
```

---

## Task 4: 최종 검증

**Files:** (변경 없음)

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
grep -rn "conversation_stt_backend\|conversation_llm_backend\|translate_backend" *.py jarvis-web/src/static/app.html | grep -v "test_\|docs/" || echo "CLEAN"
.venv/bin/python -c "import main, settings, llm, conversation_stt, live_translate; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){bad=1;console.error(e.message);}}if(bad)process.exit(1);console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `CLEAN`, `import ok`, 전체 통과, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포/재시작 후)**
- 설정 모달에 "STT"·"LLM" 2행만 표시.
- STT=로컬 변경 → 미팅·일반 모두 RealtimeSTT; STT=Gladia → 둘 다 Gladia.
- LLM=로컬 변경 → 미팅 번역·일반 대화 모두 Ollama; LLM=딥시크 → 둘 다 DeepSeek(jarvis 로그 "[llm] 대화 LLM 백엔드: …").

---

## 비고
- 구버전 `setting.yaml`(translate_backend 등)은 load 시 llm_backend 로 이주, 나머지 구 키는 무시.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
