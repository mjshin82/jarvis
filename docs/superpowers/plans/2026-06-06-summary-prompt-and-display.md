# 회의 요약 개선 (프롬프트 + 표시) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요약을 페르소나+마크다운으로 생성하고(언어별), 자막 페이지에서 대화 폭에 맞춘 박스 안에 언어 라디오로 하나씩 marked 로 렌더한다.

**Architecture:** `llm.summarize` 시스템 프롬프트를 페르소나+마크다운 규칙으로 교체(출력 언어 = lang_name). `viewer.html` 에 marked.umd.js 를 인라인 벤도링하고, `#summary` 를 980px 중앙 컬럼으로 맞춘 뒤 언어 라디오 + `marked.parse` 로 단일 언어 렌더. 데이터 흐름(summaries={lang:markdown})은 무변경.

**Tech Stack:** Python 3.11 + pytest; 정적 HTML 인라인 JS(`node --check`); marked v18.0.5 UMD(벤도링).

**스펙:** `docs/superpowers/specs/2026-06-06-summary-prompt-and-display-design.md`

**전제:** 회의 요약·종료열람 머지됨. 와이어 무변경(summaries dict). jarvis 재시작 + 웹 배포.

---

## Task 1: llm.summarize — 페르소나 + 마크다운 프롬프트

**Files:** Modify `llm.py`, `tests/test_llm_summarize.py`

- [ ] **Step 1: 테스트 갱신** — `tests/test_llm_summarize.py` 의 `test_summarize_calls_client` 의 마지막 assert 들을 교체(lang_name + Markdown 지시 확인):
```python
    out = asyncio.run(llm.summarize("회의 원문", "Japanese"))
    assert out == "요약본"
    assert "Japanese" in captured["messages"][0]["content"]
    assert "Markdown" in captured["messages"][0]["content"]
    assert "회의 원문" in captured["messages"][-1]["content"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q`
Expected: FAIL (현 프롬프트에 "Markdown" 없음)

- [ ] **Step 3: 구현** — `llm.py` `summarize` 의 `messages` 를 교체(가드/호출 시그니처는 유지):
```python
        messages = [
            {"role": "system", "content": (
                f"You are an expert meeting-minutes writer. Summarize the meeting "
                f"conversation below clearly and faithfully, written ENTIRELY in {lang_name}.\n\n"
                "Format as GitHub-flavored Markdown:\n"
                "- Use ## (h2) for sections; put a --- divider line before each ## except the "
                "first. Use ### (h3) for subsections. Never use #### or deeper.\n"
                "- Use bullet points and **bold** for emphasis. Do not put quotes inside bold "
                "(write **text**, not **\"text\"**).\n"
                "- Use a Markdown table (| ... |) when comparing figures or itemized attributes.\n"
                "- For number ranges use a hyphen (2015-2020, not 2015~2020).\n\n"
                "Cover: key discussion points, decisions made, and action items (owner + task). "
                "Never invent information not in the conversation. "
                "Output ONLY the Markdown summary — no preamble, no code fences."
            )},
            {"role": "user", "content": text},
        ]
```
(`if self._mock or self.client is None or not (text or "").strip(): return ""` 가드와 `resp = await self.client.chat.completions.create(...)` 반환은 그대로.)

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add llm.py tests/test_llm_summarize.py
git commit -m "feat(meeting): 요약 프롬프트 — 회의록 페르소나 + 마크다운(언어별)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: viewer.html — #summary 폭/라디오/마크다운 렌더 + marked 자리표시

**Files:** Modify `jarvis-web/src/static/viewer.html`. 검증: JS 구문.

- [ ] **Step 1: CSS 교체** — 현재 `#summary` 관련 6줄(`#summary { ... }` 부터 `#summary .sum-body { ... }` 까지)을 교체:
```css
  #summary { max-width: 980px; margin: 0 auto; width: 100%; box-sizing: border-box;
    padding: 14px 16px; border-bottom: 2px solid var(--border); background: var(--bg); }
  #summary.hidden { display: none; }
  #summary-langs { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; font-size: 13px; }
  #summary-langs label { cursor: pointer; }
  .md { font-size: 14px; line-height: 1.5; }
  .md h2 { font-size: 17px; margin: 14px 0 6px; }
  .md h3 { font-size: 15px; margin: 12px 0 6px; }
  .md ul, .md ol { padding-left: 20px; margin: 6px 0; }
  .md hr { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
  .md table { border-collapse: collapse; margin: 8px 0; }
  .md th, .md td { border: 1px solid var(--border); padding: 4px 8px; }
  .md code { background: var(--card); padding: 1px 4px; border-radius: 4px; }
  .md pre { background: var(--card); padding: 8px; border-radius: 6px; overflow-x: auto; }
```

- [ ] **Step 2: marked 자리표시 추가** — `</style>` 다음 줄(즉 `</head>` 앞)에 한 줄 추가:
```html
  <!-- marked-lib -->
```

- [ ] **Step 3: #summary 구조 교체** — `<div id="summary" class="hidden"></div>` 를:
```html
  <div id="summary" class="hidden">
    <div id="summary-langs"></div>
    <div id="summary-body" class="md"></div>
  </div>
```

- [ ] **Step 4: renderSummaries 교체** — 현 `function renderSummaries(summaries) { ... }` 전체를:
```javascript
  let _summaries = {}, _summaryLang = "";
  function renderSummaryBody() {
    const md = _summaries[_summaryLang] || "";
    $("summary-body").innerHTML = (typeof marked !== "undefined" && md) ? marked.parse(md) : escapeHtml(md);
  }
  function renderSummaries(summaries) {
    const el = $("summary");
    _summaries = summaries || {};
    const langs = Object.keys(_summaries);
    if (!langs.length) { el.classList.add("hidden"); return; }
    if (!_summaries[_summaryLang]) _summaryLang = langs[0];   // 선택 유지, 없으면 첫 언어
    const names = { ko: "🇰🇷 한국어", en: "🇺🇸 English", ja: "🇯🇵 日本語", zh: "🇨🇳 中文" };
    const lc = $("summary-langs");
    lc.innerHTML = "";
    for (const lg of langs) {
      const label = document.createElement("label");
      label.innerHTML = `<input type="radio" name="sumlang" value="${lg}" ${lg === _summaryLang ? "checked" : ""}> ${names[lg] || lg}`;
      label.querySelector("input").addEventListener("change", () => { _summaryLang = lg; renderSummaryBody(); });
      lc.appendChild(label);
    }
    renderSummaryBody();
    el.classList.remove("hidden");
  }
```

- [ ] **Step 5: JS 구문 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
```
Expected: `viewer JS OK`. (marked 미임베드 상태 — `typeof marked` 가드로 런타임 안전, 구문 OK.)

- [ ] **Step 6: 커밋**
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): 요약 박스 폭 맞춤 + 언어 라디오 + 마크다운 렌더(marked 자리표시)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: viewer.html — marked.umd.js 인라인 벤도링

**Files:** Modify `jarvis-web/src/static/viewer.html`(자리표시 → marked 임베드). 검증: JS 구문 + 전역 marked.

- [ ] **Step 1: marked 임베드(스크립트 splice)** — 저장소 루트에서 실행. npm 으로 marked@18.0.5 를 받아 `<!-- marked-lib -->` 자리에 `<script>…</script>` 로 인라인. 멱등(이미 임베드면 skip):
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e '
const fs=require("fs"),cp=require("child_process"),os=require("os"),path=require("path");
const v="jarvis-web/src/static/viewer.html";
let html=fs.readFileSync(v,"utf8");
if(html.includes("marked v18")){console.log("already embedded");process.exit(0);}
if(!html.includes("<!-- marked-lib -->")){console.error("placeholder <!-- marked-lib --> 없음");process.exit(1);}
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),"marked-"));
cp.execSync("npm pack marked@18.0.5",{cwd:tmp,stdio:"ignore"});
cp.execSync("tar -xzf marked-18.0.5.tgz",{cwd:tmp,stdio:"ignore"});
const lib=fs.readFileSync(path.join(tmp,"package/lib/marked.umd.js"),"utf8");
if(lib.includes("</script")){console.error("marked 소스에 </script> 포함 — 인라인 불가");process.exit(1);}
html=html.replace("<!-- marked-lib -->","<script>\n"+lib+"\n</script>");
fs.writeFileSync(v,html);
console.log("embedded marked.umd.js ("+lib.length+" bytes)");
'
```
Expected: `embedded marked.umd.js (~42921 bytes)`.

- [ ] **Step 2: 임베드 검증** — 전역 marked.parse 동작 + 자리표시 소진 + 전체 JS 구문:
```bash
cd /Users/oracle/Documents/concode/jarvis
grep -c "marked v18" jarvis-web/src/static/viewer.html        # 1
grep -c "<!-- marked-lib -->" jarvis-web/src/static/viewer.html # 0
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
```
Expected: `1`, `0`, `viewer JS OK`.

- [ ] **Step 3: 커밋**
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): marked.umd.js(v18.0.5) 인라인 벤도링 — 요약 마크다운 렌더

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

(npm 오프라인이라 `npm pack` 실패 시 BLOCKED 으로 보고 — 대안: 빌트인 경량 렌더러로 폴백 결정 필요.)

---

## Task 4: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(e.message);process.exit(1);}}console.log('viewer JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: 전체 통과, `viewer JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포·재시작 후)**
- 회의 진행(2언어 이상) → 종료 → 자막 링크 비번 입장 → 요약 패널이 대화 버블과 같은 폭, 언어 라디오로 전환, 마크다운(제목/불릿/표) 렌더.
- 언어 1개 회의: 요약 1개(라디오 1개).
- 요약 준비 전 입장 → 기록만 → 잠시 후 요약 패널 등장(meeting_summary).

---

## 비고
- 데이터(summaries={lang:markdown}) 무변경 — 프롬프트가 마크다운을 만들고 viewer 가 marked 로 렌더.
- marked v18.0.5 UMD 는 전역 `marked.parse` 노출, 소스에 `</script>` 없음(인라인 안전) — 확인됨.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
