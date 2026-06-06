# 다국어 회의 Phase B (웹) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹이 Phase A 의 제네릭 `translation`+`lang` 와이어 포맷을 렌더하고, 회의 폼에서 언어를 고르고, owner 헤더에 룸 언어를 표시한다.

**Architecture:** `types.ts`/`PUBLIC_KINDS` 를 `translation` 하나로 통합(+`lang`). viewer/app 의 `translation` 케이스를 `lang→(국기,색)` 맵으로 렌더. 회의 폼에 언어 체크박스(ko/en/jp/zh) → `meeting_start.languages`. owner badge 는 `meeting_info`(언어 포함, 이미 owner-replay) 로 표시.

**Tech Stack:** TS Cloudflare Worker(`npm run typecheck`), 정적 HTML 인라인 JS(`node --check`). JS 테스트 하니스 없음 — 게이트는 typecheck + 구문 + 수동.

**스펙:** `docs/superpowers/specs/2026-06-06-multilingual-meeting-design.md` (Phase B)

**전제:** Phase A 머지됨 — jarvis 가 `{kind:"translation", text, lang}` 발행, `meeting_start.languages` 수신, 룸 언어별 번역/요약. **이 Phase B 까지 머지·배포한 뒤** jarvis 재시작(옛 웹↔새 jarvis 깨짐 방지).

---

## Task 1: types.ts — translation kind + lang 필드

**Files:** Modify `jarvis-web/src/types.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: EventKind 교체** — 두 줄
```typescript
  | "translation_ko"       // 번역 결과 (한국어)
  | "translation_en"       // 번역 결과 (영어)
```
을 한 줄로:
```typescript
  | "translation"          // 번역 결과 (lang 필드로 대상 언어: ko|en|ja|zh)
```

- [ ] **Step 2: ClientMessage 에 lang 추가** — `ClientMessage` 인터페이스의 `text?: string;` 아래에:
```typescript
  lang?: string;
```

- [ ] **Step 3: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0. (translation_ko/en 잔존 참조가 meeting_do/viewer/app 에 있으나 .ts 타입은 통과 — meeting_do.ts 의 PUBLIC_KINDS 는 `new Set([...문자열...])` 이라 타입에러 아님. 다음 태스크에서 정리.)

- [ ] **Step 4: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts
git commit -m "feat(web): EventKind translation_ko/en → 제네릭 translation + lang 필드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: meeting_do.ts — PUBLIC_KINDS translation

**Files:** Modify `jarvis-web/src/meeting_do.ts`. 검증: `npm run typecheck`.

- [ ] **Step 1: PUBLIC_KINDS 교체** — 현재:
```typescript
const PUBLIC_KINDS = new Set([
  "hello", "source", "translation_ko", "translation_en", "partial",
  "gap", "info", "end", "kicked", "publisher_disconnected",
]);
```
를:
```typescript
const PUBLIC_KINDS = new Set([
  "hello", "source", "translation", "partial",
  "gap", "info", "end", "kicked", "publisher_disconnected",
]);
```

- [ ] **Step 2: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
Expected: exit 0.

- [ ] **Step 3: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/meeting_do.ts
git commit -m "feat(web/DO): PUBLIC_KINDS 에 translation(제네릭) — 공개 자막 유지

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: viewer.html — 제네릭 translation 렌더 + 언어 색

**Files:** Modify `jarvis-web/src/static/viewer.html`. 검증: JS 구문.

- [ ] **Step 1: CSS 변수 추가(라이트)** — `:root` 줄(`--ko: #0a7e0a; --en: #b04300; --partial: #9aa0a6;` 포함) 의 `--partial: #9aa0a6;` 뒤에 추가:
```css
 --ja: #1d4ed8; --zh: #9333ea;
```

- [ ] **Step 2: CSS 변수 추가(다크)** — 다크 모드 줄(`--ko: #7ee787; --en: #ffb86b; --partial: #6b7280;`) 의 `--partial: #6b7280;` 뒤에 추가:
```css
 --ja: #93c5fd; --zh: #d8b4fe;
```

- [ ] **Step 3: 색 클래스 추가** — `.tx.en { color: var(--en); }` 줄 아래에:
```css
  .tx.ja { color: var(--ja); }
  .tx.zh { color: var(--zh); }
```

- [ ] **Step 4: translation 케이스 교체** — `handle()` 의
```javascript
      case "translation_ko":
      case "translation_en": {
        const cls = ev.kind === "translation_ko" ? "ko" : "en";
        const sym = ev.kind === "translation_ko" ? "🌐" : "🇺🇸";
        const html = `<div class="tx ${cls}">${sym} ${escapeHtml(ev.text || "")}</div>`;
        if (lastCard) lastCard.insertAdjacentHTML("beforeend", html);
        else { const c = newCard(); c.innerHTML = html; lastCard = c; }
        break;
      }
```
를:
```javascript
      case "translation": {
        const map = { ko: ["🇰🇷", "ko"], en: ["🇺🇸", "en"], ja: ["🇯🇵", "ja"], zh: ["🇨🇳", "zh"] };
        const [sym, cls] = map[ev.lang] || ["🌐", ""];
        const html = `<div class="tx ${cls}">${sym} ${escapeHtml(ev.text || "")}</div>`;
        if (lastCard) lastCard.insertAdjacentHTML("beforeend", html);
        else { const c = newCard(); c.innerHTML = html; lastCard = c; }
        break;
      }
```

- [ ] **Step 5: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/viewer.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('viewer JS OK');"
```
Expected: `viewer JS OK`.

- [ ] **Step 6: 커밋**
```bash
git add jarvis-web/src/static/viewer.html
git commit -m "feat(web): 자막 페이지 제네릭 translation 렌더(언어별 국기/색)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: app.html — 제네릭 translation 렌더 + 언어 색

**Files:** Modify `jarvis-web/src/static/app.html`. 검증: JS 구문 + `npm run typecheck`.

- [ ] **Step 1: CSS 변수 추가(라이트/다크)** — viewer 와 동일하게, `:root` 의 `--partial: #9aa0a6;` 뒤 `--ja: #1d4ed8; --zh: #9333ea;`, 다크의 `--partial: #6b7280;` 뒤 `--ja: #93c5fd; --zh: #d8b4fe;`.

- [ ] **Step 2: 색 클래스 추가** — `.tx.en { color: var(--en); }` 아래에:
```css
  .tx.ja { color: var(--ja); }
  .tx.zh { color: var(--zh); }
```

- [ ] **Step 3: translation 케이스 교체** — `handle()` 의
```javascript
      case "translation_ko":
      case "translation_en": {
        const cls = ev.kind === "translation_ko" ? "ko" : "en";
        const sym = ev.kind === "translation_ko" ? "🌐" : "🇺🇸";
        const html = `<div class="tx ${cls}">${sym} ${escapeHtml(ev.text || "")}</div>`;
        if (lastCard) { lastCard.insertAdjacentHTML("beforeend", html); }
        else { const card = newCard(); card.innerHTML = html; lastCard = card; }
        break;
      }
```
를:
```javascript
      case "translation": {
        const map = { ko: ["🇰🇷", "ko"], en: ["🇺🇸", "en"], ja: ["🇯🇵", "ja"], zh: ["🇨🇳", "zh"] };
        const [sym, cls] = map[ev.lang] || ["🌐", ""];
        const html = `<div class="tx ${cls}">${sym} ${escapeHtml(ev.text || "")}</div>`;
        if (lastCard) { lastCard.insertAdjacentHTML("beforeend", html); }
        else { const card = newCard(); card.innerHTML = html; lastCard = card; }
        break;
      }
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('app JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `app JS OK`, typecheck 0.

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/app.html
git commit -m "feat(web): owner 회의 로그 제네릭 translation 렌더(언어별 국기/색)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: app.html — 회의 폼 언어 체크박스

**Files:** Modify `jarvis-web/src/static/app.html`. 검증: JS 구문 + `npm run typecheck`.

- [ ] **Step 1: 폼에 언어 행 추가** — `#meeting-form` 의 제목 row(`<div class="row"><div>회의 제목</div>...</div>`) **아래**, 워드북 row 위에:
```html
      <div class="row"><div>언어</div>
        <label><input type="checkbox" class="mf-lang" value="ko" checked> 한국어</label>
        <label><input type="checkbox" class="mf-lang" value="en" checked> English</label>
        <label><input type="checkbox" class="mf-lang" value="jp"> 日本語</label>
        <label><input type="checkbox" class="mf-lang" value="zh"> 中文</label>
      </div>
```

- [ ] **Step 2: 체크박스 라벨 CSS** — `<style>` 끝(`</style>` 직전)에:
```css
  #meeting-form .row label { font-size: 14px; margin-right: 12px; display: inline-block; }
  #meeting-form .row label input { margin-right: 4px; }
```

- [ ] **Step 3: menu-meet 리셋에 언어 기본 복원** — `$("menu-meet")` 핸들러의
```javascript
    $("mf-title").value = ""; $("mf-vocab").value = ""; $("mf-pass").value = "";
```
를:
```javascript
    $("mf-title").value = ""; $("mf-vocab").value = ""; $("mf-pass").value = "";
    document.querySelectorAll(".mf-lang").forEach((c) => { c.checked = (c.value === "ko" || c.value === "en"); });
```

- [ ] **Step 4: mf-start 가 languages 전송** — `mf-start` 핸들러를:
```javascript
  $("mf-start").addEventListener("click", () => {
    const title = $("mf-title").value.trim();
    const vocab = $("mf-vocab").value.split(",").map((s) => s.trim()).filter(Boolean);
    const password = $("mf-pass").value.trim();
    const languages = [...document.querySelectorAll(".mf-lang:checked")].map((c) => c.value);
    $("meeting-form").classList.add("hidden");
    showMeetingLoading();
    sendControl({ kind: "meeting_start", title, vocabulary: vocab, password, languages });
  });
```
(jp 값 그대로 전송 — jarvis 가 ja 로 정규화.)

- [ ] **Step 5: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('app JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `app JS OK`, typecheck 0.

- [ ] **Step 6: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/static/app.html
git commit -m "feat(web): 회의 폼 언어 체크박스(ko/en/jp/zh) → meeting_start.languages

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 룸 언어 badge — meeting_info 에 languages (main.py + app.html)

**Files:** Modify `main.py`, `jarvis-web/src/static/app.html`. 검증: import + JS 구문.

**근거:** hello.meta 는 web_pub 의 정적 메타(언어 기본값)라 회의별 언어를 못 담는다. 이미 owner 전용·재접속 replay 되는 `meeting_info`(Phase 2) 에 languages 를 실어 badge 로 쓴다.

- [ ] **Step 1: main.py — meeting_info 에 languages 추가** — `_after_meeting_start` 의 `web_pub.emit("meeting_info", json.dumps({...}))` 를:
```python
            web_pub.emit("meeting_info", json.dumps({
                "meeting_id": sess.meta.meeting_id,
                "password": sess.meta.password,
                "languages": list(sess.meta.languages),
            }))
```

- [ ] **Step 2: app.html — meeting_info 핸들러에서 badge 설정** — `case "meeting_info":` 의 `el.classList.remove("hidden");` 줄 **아래**(같은 try 블록 안)에:
```javascript
          const langs = Array.isArray(info.languages) ? info.languages : [];
          $("meta-badge").textContent = langs.join(" · ");
```

- [ ] **Step 3: app.html — applyMeta 의 죽은 partner_lang/user_lang badge 정리** — `applyMeta` 의
```javascript
    const tags = [];
    if (meta.partner_lang) tags.push(`${meta.partner}: ${meta.partner_lang}`);
    if (meta.user_lang) tags.push(`${user}: ${meta.user_lang}`);
    $("meta-badge").textContent = tags.join(" · ");
```
를(이제 badge 는 meeting_info 가 채움 — applyMeta 는 badge 안 건드림):
```javascript
    // 언어 badge 는 meeting_info 이벤트가 채운다(회의별 룸 언어).
```

- [ ] **Step 4: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main; print('import ok')"
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('app JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, `app JS OK`, typecheck 0.

- [ ] **Step 5: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add main.py jarvis-web/src/static/app.html
git commit -m "feat(web): owner 헤더 badge 에 룸 언어(meeting_info.languages)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
grep -rn "translation_ko\|translation_en" jarvis-web/src || echo "WEB CLEAN — no legacy translation kinds"
node -e "const fs=require('fs');for(const f of ['jarvis-web/src/static/app.html','jarvis-web/src/static/viewer.html']){const h=fs.readFileSync(f,'utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error(f,e.message);process.exit(1);}}}console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `WEB CLEAN`, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포 + jarvis 재시작 후)**
- 웹 +메뉴 미팅 → 폼에 언어 ko·en·jp 체크 → 시작. owner 헤더 badge 에 `ko · en · ja`.
- ko 발화 → owner/자막에 🇺🇸 영어 + 🇯🇵 일본어 두 줄 동시. ja 발화 → 🇰🇷 + 🇺🇸.
- 공개 자막 링크(비번 입장) 에서도 동일 렌더.
- 종료 후 `meetings.db` summary 가 `{ko:..,en:..,ja:..}` JSON.

---

## 비고
- 와이어 포맷이 Phase A 와 묶임 — **이 Phase B 머지·배포 후 jarvis 재시작**(그래야 새 jarvis 의 translation 이벤트를 새 웹이 렌더).
- 공개 viewer 에는 badge 없음(헤더 단순). 언어는 자막 줄의 국기로 드러남.
- 배포: 웹 `wrangler deploy`(머지 시 자동). jarvis 재시작 + origin push 직접.
