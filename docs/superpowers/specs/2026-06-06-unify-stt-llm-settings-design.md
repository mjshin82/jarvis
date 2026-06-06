# STT·LLM 설정 일원화 설계

날짜: 2026-06-06

## 목표
미팅/일반으로 쪼개진 STT·LLM 설정 4개를 **통합 2개**로 합쳐, 한 토글이 미팅·일반 양쪽에 적용되게 한다.
- `stt_backend`(gladia|local) — 미팅 STT + 일반 대화 STT 공통.
- `llm_backend`(deepseek|local) — 미팅 번역 LLM + 일반 대화 LLM 공통.

성격: 설정 통합 리팩터. 동작 의미는 보존(같은 backend 들), 설정 표면만 단순화.

## 비범위 (YAGNI)
- backend 구현(GladiaSTT/RealtimeSTTAdapter/LLM/coach) 로직 변경 없음 — 읽는 설정 키만 통합.
- STT/LLM 외 다른 설정 추가 없음.

## 변경 매핑
| 기존 키 | 통합 후 |
|---|---|
| `stt_backend`(미팅) | `stt_backend` (유지, 일반도 사용) |
| `conversation_stt_backend`(일반) | 제거 → `stt_backend` |
| `translate_backend`(미팅 번역) | 제거 → `llm_backend` |
| `conversation_llm_backend`(일반) | 제거 → `llm_backend` |

## settings.py
- `DEFAULTS`:
  ```python
  DEFAULTS = {
      "stt_backend": "gladia",                                                  # gladia | local
      "llm_backend": "local" if config.LLM_BACKEND == "local" else "deepseek",  # deepseek | local
  }
  ```
- `ALLOWED`:
  ```python
  ALLOWED = {
      "stt_backend": {"gladia", "local"},
      "llm_backend": {"deepseek", "local"},
  }
  ```
- `load()` 에 **구버전 이주**(검증 `_valid` 전, raw data 에서):
  ```python
  # 구버전 키 이주: llm_backend 없으면 옛 키에서 가져옴(translate/conversation_llm).
  if "llm_backend" not in data:
      old = data.get("conversation_llm_backend") or data.get("translate_backend")
      if old:
          data["llm_backend"] = old
  # stt_backend 는 기존 키 그대로 유지. conversation_stt_backend 는 무시.
  ```
  (이후 기존 `_current.update(_valid(data))` — `_valid` 가 ALLOWED 밖(구 키) 자동 제거.)

## 코드 변경 (읽는 키만)
- `conversation_stt.py` `_backend()`: `self._settings_get("conversation_stt_backend")` → `self._settings_get("stt_backend")`.
- `live_translate.py` `_setup_translator()`: `settings.get("translate_backend") == "deepseek"` → `settings.get("llm_backend") == "deepseek"`. (STT 읽기 `settings.get("stt_backend")` 는 무변경.)
- `llm.py` `__init__`: `self.set_backend(settings.get("conversation_llm_backend"))` → `settings.get("llm_backend")`.
- `main.py` `_on_remote_command` apply_settings: `llm.set_backend(settings.get("conversation_llm_backend"))` → `settings.get("llm_backend")`.
- `commands.py` `_reload_settings`: `_llm.set_backend(settings.get("conversation_llm_backend"))` → `settings.get("llm_backend")`.

## 웹 (app.html)
설정 모달 4행 → **2행**:
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
- `set-translate`, `set-conv-stt`, `set-conv-llm` 행 제거. `set-stt` 유지, `set-llm` 신규(기존 set-conv-llm/set-translate 대체).
- `fillSettings(s)`:
  ```javascript
  function fillSettings(s) {
    document.querySelectorAll('input[name="set-stt"]').forEach((r) => { r.checked = (r.value === s.stt_backend); });
    document.querySelectorAll('input[name="set-llm"]').forEach((r) => { r.checked = (r.value === s.llm_backend); });
  }
  ```
- `curSettings()`:
  ```javascript
  function curSettings() {
    const s = document.querySelector('input[name="set-stt"]:checked');
    const l = document.querySelector('input[name="set-llm"]:checked');
    return { stt_backend: s ? s.value : "gladia", llm_backend: l ? l.value : "deepseek" };
  }
  ```

## 데이터 흐름
웹 설정 변경 → apply_settings → `settings.apply` + `llm.set_backend(settings.get("llm_backend"))`.
- 미팅 STT/일반 STT → `settings.get("stt_backend")`.
- 미팅 번역/일반 대화 LLM → `llm_backend`(번역은 _setup_translator, 대화는 LLM.set_backend).

## 테스트
- `tests/test_settings.py`(보강/신규): `current()`/`DEFAULTS` 에 `stt_backend`·`llm_backend` 만, 구 키 부재.
  `apply({"stt_backend":"local","llm_backend":"local"})` 반영. 구 키 apply 무시(ALLOWED 밖).
  이주: 구버전 dict(`{"translate_backend":"local"}` 또는 `{"conversation_llm_backend":"local"}`)를 `load(path=tmp)` →
  `get("llm_backend") == "local"`.
- 기존 `test_conversation_stt.py`/`test_llm_backend.py`: settings 키에 비의존(설정값만 확인)이라 유지 — 단
  `LLM()` 생성 시 `settings.get("llm_backend")` 가 유효해야 함(기본값 존재 → OK).
- 웹: `npm run typecheck` + JS 구문 + 수동.

## 검증
- `.venv/bin/python -m pytest -q` 통과 + `import main, settings, llm, conversation_stt, live_translate`.
- `cd jarvis-web && npm run typecheck` 0, app.html JS 구문 OK.
- 수동(배포/재시작 후): 설정 모달에 STT·LLM 2행만; STT 변경 시 미팅·일반 모두 반영, LLM 변경 시 번역·대화 모두 반영.

## 영향 파일
| 파일 | 변경 |
|------|------|
| `settings.py` | DEFAULTS/ALLOWED 2키 + load 이주 |
| `conversation_stt.py` | `stt_backend` 읽기 |
| `live_translate.py` | `llm_backend` 읽기(번역) |
| `llm.py` | `llm_backend` 읽기 |
| `main.py`, `commands.py` | `llm_backend` 읽기 |
| `jarvis-web/src/static/app.html` | 설정 모달 2행 + fill/curSettings |
| `tests/test_settings.py` | 통합/이주 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
