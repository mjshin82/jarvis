# 모드 전환 효과 + 일반 대화 LLM 선택 설계

날짜: 2026-06-06

## 목표
- **G**: 웹에서 모드 전환(홈↔회의 등)이 "팍" 바뀌어 인지가 안 되는 문제 → 토스트 배너 + 페이드.
- **H**: 일반 대화 LLM 을 딥시크/로컬에서 선택하는 옵션(STT/번역 옵션과 동일한 라이브 스위치).

성격: 기능 추가. 기본 동작 보존(H 기본 deepseek, mock 은 그대로).

## 비범위 (YAGNI)
- 미팅 번역 LLM 경로(`live_translate._setup_translator`, `translate_backend`) 변경 없음.
- jarvis 콘솔 UI 전환 효과(콘솔은 텍스트 로그로 이미 모드 표시).
- LLM 의 도구/히스토리/warmup 로직 변경 없음 — `set_backend` 는 client/model/extra 만 교체.

---

## G — 모드 전환 토스트 + 페이드 (web `app.html`)

### CSS
```css
  #mode-toast { position: fixed; top: 64px; left: 50%; transform: translateX(-50%) translateY(-8px);
    background: var(--accent); color: #fff; padding: 8px 16px; border-radius: 999px; font-size: 14px;
    z-index: 40; opacity: 0; pointer-events: none; transition: opacity .25s, transform .25s; box-shadow: 0 2px 10px #0005; }
  #mode-toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  @keyframes viewfade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
```

### HTML
`#meeting-loading` 근처에 추가:
```html
  <div id="mode-toast"></div>
```

### JS
- `showView(v)` (뷰가 실제 바뀔 때만 실행되는 기존 함수) 의 `setView(nv)` 다음에:
  ```javascript
    const el = nv === "meeting" ? $("meeting-view") : $("home-view");
    el.style.animation = "none"; void el.offsetWidth; el.style.animation = "viewfade .25s ease";
    showModeToast(nv === "meeting" ? "🎤 회의 모드" : "💬 일반 모드");
  ```
- 토스트 헬퍼:
  ```javascript
  let modeToastTimer = null;
  function showModeToast(text) {
    const t = $("mode-toast");
    t.textContent = text; t.classList.add("show");
    clearTimeout(modeToastTimer);
    modeToastTimer = setTimeout(() => t.classList.remove("show"), 1500);
  }
  ```
- 초기 `setView("home")`(init)은 showView 가 아니므로 토스트/페이드 없음. jarvis `navigate`(회의 시작/종료/복구)는 showView 경유 → 자동 포함.

---

## H — 일반 대화 LLM 백엔드 선택 (Python + web)

### settings.py
- `ALLOWED["conversation_llm_backend"] = {"deepseek", "local"}`.
- 기본값은 env 의도 보존을 위해 `config.LLM_BACKEND` 에서 파생:
  `import config` 추가 후 `DEFAULTS["conversation_llm_backend"] = "local" if config.LLM_BACKEND == "local" else "deepseek"`.
  (config 는 leaf 모듈 — 순환참조 없음.)

### llm.py — `LLM.set_backend(backend)`
- 신규 메서드: 주어진 backend("deepseek"|"local")로 `self.client / self.model / self.extra / self.backend` 재구성.
  ```python
  def set_backend(self, backend: str) -> None:
      """대화 LLM 백엔드 전환(deepseek=remote / local=ollama). mock 이면 무시.
      전제 미충족 시 사용 가능한 쪽으로 폴백."""
      if self._mock:
          return
      want = "remote" if backend == "deepseek" else "local"
      has_remote = bool(config.DEEPSEEK_API_KEY and config.DEEPSEEK_API_KEY != "sk-your-key-here")
      if want == "remote" and not has_remote:
          want = "local"   # 키 없으면 로컬 폴백
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
  ```
- `__init__` 변경: mock 판별 플래그 `self._mock = (config.LLM_BACKEND == "mock")`. mock 이면 기존대로(클라이언트 None) 유지. mock 이 아니면 기존 remote/local 분기 대신 `self.set_backend(settings.get("conversation_llm_backend"))` 로 초기 구성(저장된 설정 반영). `self.extra` 초기화 라인은 set_backend 가 담당.
  - 기존 `self.client/model/extra` 를 쓰는 코드(respond, tool-calling, warmup, coach.translate)는 무변경.
  - import: `import settings` (llm.py 상단).

### main.py — 적용 시점
- 부팅 시 LLM 생성(`llm = LLM()`)이 `__init__` 안에서 이미 설정 반영(위). 추가 호출 불필요.
- 웹 설정 변경: `_on_remote_command` 의 `apply_settings` 분기에서 `settings.apply(...)` 다음에:
  ```python
  llm.set_backend(settings.get("conversation_llm_backend"))
  ```

### commands.py — /reload-settings
- `_reload_settings` 핸들러에서 `settings.load()` 다음에:
  ```python
  llm = ctx.get("llm")
  if llm is not None:
      llm.set_backend(settings.get("conversation_llm_backend"))
  ```
  (`cmd_ctx["llm"]` 이미 존재.)

### 웹 설정 모달 (app.html)
- "일반 대화 STT" 행 다음에 "일반 대화 LLM: 딥시크 / 로컬" 행 추가(name `set-conv-llm`, value `deepseek`/`local`).
- `fillSettings`: `set-conv-llm` 을 `s.conversation_llm_backend` 로 체크.
- `curSettings`: `conversation_llm_backend` 포함(기본 `"deepseek"`).

---

## 데이터 흐름
- G: navigate/토글 → showView(변경) → 페이드 애니메이션 + 토스트.
- H: 웹 설정 라디오 변경 → apply_settings → settings.apply + `llm.set_backend` → 다음 발화부터 새 backend.

## 테스트
- `tests/test_llm_backend.py`(신규): config monkeypatch(DEEPSEEK_API_KEY 유무) 후
  `LLM().set_backend("local")` → `model==config.LOCAL_MODEL`, `extra` 에 keep_alive;
  `set_backend("deepseek")`(키 있음) → `model==config.DEEPSEEK_MODEL`, `extra=={}`;
  키 없음+deepseek → local 폴백; mock 모드면 set_backend no-op(client None 유지).
  (LLM 생성은 네트워크 없음 — AsyncOpenAI 객체만.)
- settings 기본/허용에 `conversation_llm_backend` 포함 확인(test_settings 보강 또는 신규).
- G/web: `npm run typecheck` + JS 구문검사 + 수동.

## 검증
- `.venv/bin/python -m pytest -q` 통과 + `import main, llm`.
- `cd jarvis-web && npm run typecheck` 0, app.html JS 구문 OK.
- 수동(배포/재시작 후): 홈↔회의 전환 시 토스트+페이드(G); 설정에서 일반 대화 LLM=로컬/딥시크 전환 후 발화가 해당 backend 로(H).

## 영향 파일
| 파일 | 변경 |
|------|------|
| `settings.py` | `conversation_llm_backend` |
| `llm.py` | `set_backend` + `__init__` 가 설정 반영(+ import settings, `_mock`) |
| `main.py` | apply_settings 시 `llm.set_backend` |
| `commands.py` | /reload-settings 시 `llm.set_backend` |
| `jarvis-web/src/static/app.html` | G(토스트+페이드) + 설정 LLM 라디오 행 |
| `tests/test_llm_backend.py`(신규) | set_backend 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
