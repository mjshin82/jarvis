# 회의 요약 개편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 언어별 요약은 그 언어 텍스트만 모아 전용 상위 모델(deepseek-v4-pro+thinking)로 생성하고, 자막 페이지는 대화기록/요약을 라디오로 전환한다.

**Architecture:** `meeting_store.lang_text` 가 트랜스크립트에서 한 언어 텍스트만 추출(소스언어=룸언어−번역키). `llm.summarize` 가 deepseek 전용 클라이언트+v4-pro+thinking 로 호출(대화·번역은 v4-flash). viewer.html 이 대화기록+요약을 통합 라디오 탭으로 전환.

**Tech Stack:** Python 3.11 + pytest; 정적 HTML 인라인 JS(`node --check`); DeepSeek V4 API(OpenAI 호환, extra_body thinking).

**스펙:** `docs/superpowers/specs/2026-06-07-summary-rework-design.md`

**전제:** 회의 요약·종료열람 머지됨. e7766f 재요약은 머지·재시작 후 일회성(아래 Task 6 메모).

---

## Task 1: meeting_store.lang_text — 언어별 텍스트 추출

**Files:** Modify `meeting_store.py`, `tests/test_meeting_store.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_store.py` 끝에
```python
def test_lang_text_picks_only_that_language():
    from meeting_store import lang_text
    lines = [
        {"source": "안녕하세요", "translations": {"en": "Hello"}},        # ko 소스(en 번역)
        {"source": "Nice to meet you", "translations": {"ko": "반갑습니다"}},  # en 소스(ko 번역)
    ]
    rooms = ["ko", "en"]
    assert lang_text(lines, "ko", rooms) == "안녕하세요\n반갑습니다"
    assert lang_text(lines, "en", rooms) == "Hello\nNice to meet you"


def test_lang_text_skips_empty_and_missing():
    from meeting_store import lang_text
    lines = [{"source": "안녕", "translations": {}}]   # 번역 실패(둘 다 없음) → src 불명
    # ko 소스 후보 불명(missing 2개) → source 안 씀; ko 번역도 없음 → 빈
    assert lang_text(lines, "ko", ["ko", "en", "ja"]) == ""
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q`
Expected: FAIL (lang_text 없음)

- [ ] **Step 3: 구현** — `meeting_store.py` 모듈 함수 추가(`archive_response` 근처, 클래스 밖):
```python
def lang_text(lines, lang, room_langs) -> str:
    """트랜스크립트에서 한 언어(lang)의 텍스트만 모아 합침.
    소스언어 = 그 줄의 translations 에 없는 룸 언어(번역 대상에서 빠진 것). 1개로 확정될 때만 source 사용."""
    out = []
    for e in (lines or []):
        tr = e.get("translations") or {}
        missing = [l for l in (room_langs or []) if l not in tr]
        src = missing[0] if len(missing) == 1 else None
        t = (e.get("source") or "") if lang == src else (tr.get(lang) or "")
        if t and t.strip():
            out.append(t)
    return "\n".join(out)
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_store.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add meeting_store.py tests/test_meeting_store.py
git commit -m "feat(meeting): meeting_store.lang_text — 언어별 텍스트만 추출(소스=룸−번역키)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: config — DeepSeek V4 모델

**Files:** Modify `config.py`. 검증: import.

- [ ] **Step 1: 모델명 교체 + SUMMARY_MODEL 추가**

`DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")` 를:
```python
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")            # 대화(레거시 deepseek-chat 졸업)
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "deepseek-v4-pro")               # 회의 요약 전용(상위 + thinking)
```
그리고 `MEET_REMOTE_MODEL = os.getenv("MEET_REMOTE_MODEL", "deepseek-chat")` 를:
```python
MEET_REMOTE_MODEL = os.getenv("MEET_REMOTE_MODEL", "deepseek-v4-flash")     # 회의 번역
```

- [ ] **Step 2: 검증**
```bash
.venv/bin/python -c "import config; print(config.DEEPSEEK_MODEL, config.MEET_REMOTE_MODEL, config.SUMMARY_MODEL)"
```
Expected: `deepseek-v4-flash deepseek-v4-flash deepseek-v4-pro`.

- [ ] **Step 3: 커밋**
```bash
git add config.py
git commit -m "feat(config): DeepSeek V4 — 대화/번역 v4-flash, 요약 전용 SUMMARY_MODEL(v4-pro)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: llm.summarize — 요약 전용 v4-pro + thinking

**Files:** Modify `llm.py`, `tests/test_llm_summarize.py`

- [ ] **Step 1: 테스트 교체** — `tests/test_llm_summarize.py` 의 `test_summarize_calls_client` 를 **삭제**하고 다음으로 교체(`import config` 가 파일에 있어야 함 — 없으면 추가):
```python
def _fake_client(captured):
    class FakeMsg: content = "요약본"
    class FakeChoice: message = FakeMsg()
    class FakeResp: choices = [FakeChoice()]
    class FakeCompletions:
        async def create(self, **kw): captured.update(kw); return FakeResp()
    class FakeChat: completions = FakeCompletions()
    class FakeClient: chat = FakeChat()
    return FakeClient()


def test_summarize_uses_deepseek_v4pro_with_thinking(monkeypatch):
    import config
    from llm import LLM
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k", raising=False)
    monkeypatch.setattr(config, "SUMMARY_MODEL", "sum-model", raising=False)
    llm = LLM(); llm._mock = False
    captured = {}
    llm._summary_client = _fake_client(captured)     # lazy 빌드 대신 주입
    out = asyncio.run(llm.summarize("회의 원문", "Japanese"))
    assert out == "요약본"
    assert captured["model"] == "sum-model"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "Japanese" in captured["messages"][0]["content"]
    assert "Markdown" in captured["messages"][0]["content"]
    assert "회의 원문" in captured["messages"][-1]["content"]


def test_summarize_falls_back_to_local_without_deepseek(monkeypatch):
    import config
    from llm import LLM
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    llm = LLM(); llm._mock = False
    captured = {}
    llm.client = _fake_client(captured); llm.model = "local-m"; llm.extra = {}
    out = asyncio.run(llm.summarize("회의 원문", "Korean"))
    assert out == "요약본"
    assert captured["model"] == "local-m"
    assert captured["extra_body"] == {}            # 폴백 — thinking 없음
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q`
Expected: FAIL (현 summarize 는 self.model 사용 / _summary_client 없음)

- [ ] **Step 3: 구현** — `llm.py`

(a) `__init__` 의 `self.extra = {}` 줄 아래에:
```python
        self._summary_client = None     # 요약 전용 deepseek 클라이언트(lazy)
```
(b) `summarize` 메서드를 교체:
```python
    def _summary_target(self):
        """요약 전용 타겟 — deepseek 키 있으면 v4-pro+thinking, 없으면 대화 백엔드 폴백."""
        key = config.DEEPSEEK_API_KEY
        if key and key != "sk-your-key-here":
            if self._summary_client is None:
                self._summary_client = AsyncOpenAI(api_key=key, base_url=config.DEEPSEEK_BASE_URL)
            return self._summary_client, config.SUMMARY_MODEL, {"thinking": {"type": "enabled"}}
        return self.client, self.model, dict(self.extra or {})

    async def summarize(self, text: str, lang_name: str = "Korean") -> str:
        """회의 트랜스크립트 1회 요약(대상 언어). 요약 전용 모델 사용. mock/빈 → ""."""
        if self._mock or not (text or "").strip():
            return ""
        client, model, extra = self._summary_target()
        if client is None:
            return ""
        messages = [
            {"role": "system", "content": (
                f"You are an expert meeting-minutes writer. Summarize the meeting "
                f"conversation below clearly and faithfully, written ENTIRELY in {lang_name}.\n\n"
                "Format as GitHub-flavored Markdown:\n"
                "- Use ## (h2) for sections; put a --- divider line before each ## except the "
                "first. Use ### (h3) for subsections. Never use #### or deeper.\n"
                "- Use bullet points and **bold** for emphasis. Do not put quotes inside bold "
                "(write **text**, not **\"text\"**).\n"
                "- For number ranges use a hyphen (2015-2020, not 2015~2020).\n\n"
                "Be concise and proportional to the conversation: a short conversation needs only "
                "a few bullets and may have no section headings at all. The summary MUST be shorter "
                "than the conversation — never pad, repeat, or invent. Summarize only what was "
                "actually said: key points, decisions, and action items (if any). "
                "Output ONLY the Markdown summary — no preamble, no code fences."
            )},
            {"role": "user", "content": text},
        ]
        resp = await client.chat.completions.create(
            model=model, messages=messages, extra_body=extra,
        )
        return (resp.choices[0].message.content or "").strip()
```
(`AsyncOpenAI`·`config` 는 llm.py 상단에 이미 import 됨.)

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add llm.py tests/test_llm_summarize.py
git commit -m "feat(llm): 요약 전용 모델(deepseek-v4-pro)+thinking, 대화/번역과 분리

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: main._save_meeting — 언어별 텍스트로 요약

**Files:** Modify `main.py`. 검증: import + pytest.

- [ ] **Step 1: import 에 lang_text 추가** — `from meeting_store import MeetingStore, archive_response` 를:
```python
from meeting_store import MeetingStore, archive_response, lang_text
```

- [ ] **Step 2: _save_meeting 요약 루프 교체** — `_run` 안의 `def _line_text(e): ...` 부터 요약 `for lc in langs:` 루프 직전까지(혼합 텍스트 빌드)를 제거하고, 언어별 텍스트로 요약. 구체적으로 현재:
```python
            def _line_text(e):
                src = e.get("source") or ""
                tx = " / ".join(v for v in (e.get("translations") or {}).values() if v)
                return src + (f" / {tx}" if tx else "")
            text = "\n".join(_line_text(e) for e in lines)
            langs = record.get("languages") or ["ko"]
            summaries = {}
            for lc in langs:
                try:
                    s = await llm.summarize(text, languages.NAMES.get(lc, lc))
                except Exception as e:
                    console.log(f"회의 요약 실패({lc}): {e}")
                    continue
                if s:
                    summaries[lc] = s
```
를:
```python
            langs = record.get("languages") or ["ko"]
            summaries = {}
            for lc in langs:
                lc_text = lang_text(lines, lc, langs)       # 그 언어 텍스트만
                if not lc_text.strip():
                    continue
                try:
                    s = await llm.summarize(lc_text, languages.NAMES.get(lc, lc))
                except Exception as e:
                    console.log(f"회의 요약 실패({lc}): {e}")
                    continue
                if s:
                    summaries[lc] = s
```

- [ ] **Step 3: 검증**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 4: 커밋**
```bash
git add main.py
git commit -m "feat(meeting): 요약을 언어별 텍스트(lang_text)로 — 혼합 텍스트 폐기

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: viewer.html — 대화기록 + 요약 통합 라디오

**Files:** Modify `jarvis-web/src/static/viewer.html`. 검증: JS 구문.

- [ ] **Step 1: CSS 교체** — `#summary { ... }` 부터 `#summary-body { ... }` 까지(현재 9줄, line 59~67)를 교체(`.md` 규칙은 그대로):
```css
  #view-tabs { max-width: 980px; margin: 0 auto; width: 100%; box-sizing: border-box;
    padding: 12px 16px 0; display: flex; gap: 8px; flex-wrap: wrap; }
  #view-tabs.hidden { display: none; }
  #view-tabs label { cursor: pointer; user-select: none; font-size: 14px; padding: 5px 14px;
    border: 1px solid var(--border); border-radius: 999px; background: var(--bg); }
  #view-tabs input { position: absolute; opacity: 0; width: 0; height: 0; }
  #view-tabs label:has(input:checked) { background: var(--accent); color: #fff; border-color: var(--accent); }
  #summary-body { max-width: 980px; margin: 10px auto; width: 100%; box-sizing: border-box;
    background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  #summary-body.hidden { display: none; }
  #log.hidden { display: none; }
```

- [ ] **Step 2: HTML 교체** — 현재:
```html
  <div id="summary" class="hidden">
    <div id="summary-langs"></div>
    <div id="summary-body" class="md"></div>
  </div>
  <main id="log"></main>
```
를:
```html
  <div id="view-tabs" class="hidden"></div>
  <div id="summary-body" class="md hidden"></div>
  <main id="log"></main>
```

- [ ] **Step 3: JS 교체** — 현재 `let _summaries = {}, _summaryLang = "";` 부터 `renderSummaries` 함수 끝까지(line 213~235)를 교체:
```javascript
  let _summaries = {}, _tab = "log";   // "log"(대화기록) | 언어코드
  function applyTab() {
    if (_tab === "log") {
      $("log").classList.remove("hidden");
      $("summary-body").classList.add("hidden");
    } else {
      $("log").classList.add("hidden");
      const md = _summaries[_tab] || "";
      $("summary-body").innerHTML = (typeof marked !== "undefined" && md) ? marked.parse(md) : escapeHtml(md);
      $("summary-body").classList.remove("hidden");
    }
  }
  function renderSummaries(summaries) {
    _summaries = summaries || {};
    const langs = Object.keys(_summaries);
    const tabs = $("view-tabs");
    if (!langs.length) { tabs.classList.add("hidden"); _tab = "log"; applyTab(); return; }
    if (_tab !== "log" && !_summaries[_tab]) _tab = "log";     // 선택 유지, 없으면 대화기록
    const names = { ko: "🇰🇷 한국어", en: "🇺🇸 English", ja: "🇯🇵 日本語", zh: "🇨🇳 中文" };
    const opts = [["log", "💬 대화기록"]].concat(langs.map((lg) => [lg, (names[lg] || lg) + " 요약"]));
    tabs.innerHTML = "";
    for (const [val, txt] of opts) {
      const label = document.createElement("label");
      label.innerHTML = `<input type="radio" name="vtab" value="${val}" ${val === _tab ? "checked" : ""}> ${txt}`;
      label.querySelector("input").addEventListener("change", () => { _tab = val; applyTab(); });
      tabs.appendChild(label);
    }
    tabs.classList.remove("hidden");
    applyTab();
  }
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `viewer JS OK`, typecheck 0.

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): 자막 페이지 대화기록/요약 통합 라디오 탭

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, llm, config, meeting_store; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(e.message);process.exit(1);}}console.log('viewer JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `viewer JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포·재시작 후)**
- 새 2언어 회의 → 종료 → 요약이 언어별로 정확(ko 요약=한국어 내용만, en 요약=영어 내용만).
- 자막 링크 입장 → 상단 라디오 [💬 대화기록][🇰🇷 한국어 요약][🇺🇸 English 요약] 전환 동작, 기본 대화기록.

- [ ] **Step 3: e7766f 재요약 (일회성, 컨트롤러가 직접 실행)** — jarvis 환경(deepseek 키)에서:
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "
import asyncio, json, meeting_store, languages
from meeting_store import MeetingStore, lang_text
from llm import LLM
store = MeetingStore('meetings.db'); llm = LLM()
row = store.get('e7766f'); lines = json.loads(row['transcript'] or '[]')
langs = sorted({l for e in lines for l in (e.get('translations') or {})} | {'ko','en'})
async def run():
    out = {}
    for lc in langs:
        t = lang_text(lines, lc, langs)
        if t.strip():
            s = await llm.summarize(t, languages.NAMES.get(lc, lc))
            if s: out[lc] = s
    store.set_summary('e7766f', json.dumps(out, ensure_ascii=False))
    print('e7766f 재요약:', list(out))
asyncio.run(run())
"
```
(이 스텝은 머지·jarvis 재시작 후 컨트롤러가 직접 실행. langs 는 트랜스크립트 번역키 ∪ {ko,en} 로 추정 — e7766f 가 ko/en 이면 정확.)

---

## 비고
- 소스언어는 LLM 없이 도출(룸언어 − 번역키). 2언어 방은 항상 확정.
- 요약 전용 클라이언트는 deepseek 키 있을 때만; 없으면 local 폴백(thinking 없음).
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
