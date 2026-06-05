# SP2 — 하단 입력 dock + `+` 메뉴 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 홈 하단에 음성 버튼을 감싸는 고정 dock + `+` 기능 메뉴(미팅모드)를 만들고, 회의 뷰에선 그 자리에 회의 종료 버튼을 노출한다.

**Architecture:** 떠 있던 원형 음성 버튼·상단 회의종료 바를 하나의 하단 고정 `#dock`(음성 pill + `+`/`회의 종료`)으로 통합. `+` 는 `#plus-menu` 를 토글하고, "미팅모드" 항목이 control 채널로 `meeting_start` 를 보내 jarvis 회의를 시작(→ SP1 navigate 로 웹 전환).

**Tech Stack:** 바닐라 JS/HTML/CSS · Python(main.py 1줄) · Cloudflare Worker(정적).

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`.

---

## Task 1: main.py — meeting_start control 명령

**Files:** Modify `main.py`

- [ ] **Step 1: _on_remote_command 분기 추가** — 현재:
```python
        async def _on_remote_command(kind):
            nonlocal hands_free, response, watchdog
            if kind == "meeting_stop":
                await stop_meeting()
            elif kind == "listen_start":
```
를 다음으로 교체(meeting_start 분기 추가):
```python
        async def _on_remote_command(kind):
            nonlocal hands_free, response, watchdog
            if kind == "meeting_stop":
                await stop_meeting()
            elif kind == "meeting_start":
                await start_meeting_setup()   # 성공 시 _begin_meeting 이 navigate(meeting) 발행
            elif kind == "listen_start":
```

- [ ] **Step 2: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 3: 커밋**
```bash
git add main.py
git commit -m "feat(SP2): main — meeting_start control 명령(웹 + 메뉴에서 회의 시작)"
```

---

## Task 2: app.html — 하단 dock + 메뉴

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: CSS 교체** — 현재 `<style>` 안의 controls/meeting-stop/voice-toggle 블록(아래 전체):
```css
  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
    padding: 10px 14px; border-bottom: 1px solid var(--border); }
  body[data-view="home"] #controls { display: none; }   /* 홈은 음성 버튼만 사용 */
  button { font-size: 15px; padding: 8px 16px; border-radius: 10px; border: none; background: var(--accent); color: #fff; cursor: pointer; }
  button.off { background: #dc2626; }
  #meeting-stop { display: none; background: #dc2626; }
  body[data-view="meeting"] #meeting-stop { display: inline-block; }
  #voice-toggle {
    position: fixed; right: 18px; bottom: 18px; z-index: 15;
    width: 56px; height: 56px; border-radius: 50%; padding: 0; border: none;
    background: #111; color: #fff; cursor: pointer;
    display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 10px #0005;
  }
  #voice-toggle.active { background: #dc2626; animation: vpulse 1.2s ease-in-out infinite; }
  @keyframes vpulse { 0%, 100% { box-shadow: 0 0 0 0 #dc262688; } 50% { box-shadow: 0 0 0 9px #dc262600; } }
  body[data-view="meeting"] #voice-toggle { display: none; }
```
을 다음으로 교체:
```css
  button { font-size: 15px; padding: 8px 16px; border-radius: 10px; border: none; background: var(--accent); color: #fff; cursor: pointer; }
  /* 하단 입력 dock */
  #dock {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 15;
    display: flex; align-items: center; gap: 10px;
    padding: 10px 14px calc(10px + env(safe-area-inset-bottom));
    background: var(--bg); border-top: 1px solid var(--border);
  }
  #voice-toggle {
    flex: 1; height: 48px; border-radius: 24px; border: none; cursor: pointer;
    background: #111; color: #fff; font-size: 15px;
    display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  #voice-toggle.active { background: #dc2626; animation: vpulse 1.2s ease-in-out infinite; }
  @keyframes vpulse { 0%, 100% { box-shadow: 0 0 0 0 #dc262688; } 50% { box-shadow: 0 0 0 9px #dc262600; } }
  #dock-plus {
    flex: none; width: 48px; height: 48px; border-radius: 50%; padding: 0; border: none;
    background: var(--accent); color: #fff; font-size: 26px; line-height: 1; cursor: pointer;
  }
  #meeting-stop { flex: 1; height: 48px; border-radius: 24px; background: #dc2626; }
  body[data-view="home"] #meeting-stop { display: none; }
  body[data-view="meeting"] #voice-toggle,
  body[data-view="meeting"] #dock-plus { display: none; }
  /* + 메뉴 */
  #plus-menu {
    position: fixed; right: 14px; bottom: 76px; z-index: 16;
    display: flex; flex-direction: column; gap: 8px;
    background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 8px;
    box-shadow: 0 4px 16px #0006;
  }
  #plus-menu.hidden { display: none; }
  #plus-menu button { background: var(--card); color: var(--fg); text-align: left; white-space: nowrap; }
  /* dock 가림 방지 */
  #chat, #log { padding-bottom: 80px; }
```

- [ ] **Step 2: lockbar 오프셋 상향** — 현재:
```css
  .lockbar { position: fixed; bottom: 48px; left: 50%; transform: translateX(-50%);
```
을 다음으로(dock 위로):
```css
  .lockbar { position: fixed; bottom: 84px; left: 50%; transform: translateX(-50%);
```

- [ ] **Step 3: DOM 재구성** — 현재(상단 controls + 떠있는 voice-toggle):
```html
  <div id="controls">
    <button id="meeting-stop">🛑 회의 종료</button>
  </div>

  <div id="home-view">
    <main id="chat"></main>
  </div>
  <button id="voice-toggle" aria-label="음성 대화">
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
      <rect x="3" y="8" width="2.5" height="6" rx="1.25" fill="currentColor"/>
      <rect x="7.5" y="5" width="2.5" height="12" rx="1.25" fill="currentColor"/>
      <rect x="12" y="3" width="2.5" height="16" rx="1.25" fill="currentColor"/>
      <rect x="16.5" y="7" width="2.5" height="8" rx="1.25" fill="currentColor"/>
    </svg>
  </button>
  <div id="meeting-view">
    <main id="log"></main>
    <div class="lockbar" id="lockbar">↓ 새 자막 보기</div>
  </div>
```
을 다음으로 교체(상단 controls 제거, dock 신설):
```html
  <div id="home-view">
    <main id="chat"></main>
  </div>
  <div id="meeting-view">
    <main id="log"></main>
    <div class="lockbar" id="lockbar">↓ 새 자막 보기</div>
  </div>

  <div id="plus-menu" class="hidden">
    <button id="menu-meet">🎤 미팅모드</button>
  </div>
  <div id="dock">
    <button id="voice-toggle" aria-label="음성 대화">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
        <rect x="3" y="8" width="2.5" height="6" rx="1.25" fill="currentColor"/>
        <rect x="7.5" y="5" width="2.5" height="12" rx="1.25" fill="currentColor"/>
        <rect x="12" y="3" width="2.5" height="16" rx="1.25" fill="currentColor"/>
        <rect x="16.5" y="7" width="2.5" height="8" rx="1.25" fill="currentColor"/>
      </svg>
      <span>음성으로 대화</span>
    </button>
    <button id="dock-plus" aria-label="메뉴">+</button>
    <button id="meeting-stop">🛑 회의 종료</button>
  </div>
```

- [ ] **Step 4: setView 에서 메뉴 닫기** — 현재:
```js
  function setView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    document.body.dataset.view = nv;
    $("title").textContent = nv === "meeting" ? meetingTitle : "🤖 Jarvis";
  }
```
을 다음으로:
```js
  function setView(v) {
    const nv = (v === "meeting" ? "meeting" : "home");
    document.body.dataset.view = nv;
    $("title").textContent = nv === "meeting" ? meetingTitle : "🤖 Jarvis";
    $("plus-menu").classList.add("hidden");   // 뷰 전환 시 메뉴 닫기
  }
```

- [ ] **Step 5: + 메뉴 핸들러 추가** — 기존 meeting-stop 핸들러(아래) 다음에 추가:
```js
  // ---- 회의 종료 (웹 → control) ----
  $("meeting-stop").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    sendControl({ kind: "meeting_stop" });
  });
```
이 블록 **다음**에 삽입:
```js
  // ---- + 기능 메뉴 ----
  $("dock-plus").addEventListener("click", (e) => {
    e.stopPropagation();
    $("plus-menu").classList.toggle("hidden");
  });
  $("menu-meet").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    sendControl({ kind: "meeting_start" });
    $("plus-menu").classList.add("hidden");
  });
  document.addEventListener("click", (e) => {
    const menu = $("plus-menu");
    if (!menu.classList.contains("hidden") && !menu.contains(e.target) && e.target !== $("dock-plus")) {
      menu.classList.add("hidden");
    }
  });
```

- [ ] **Step 6: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/appd2.js && node --check /tmp/appd2.js && echo "JS OK"` → `JS OK`
`grep -c 'id="dock"\|id="dock-plus"\|id="plus-menu"\|id="menu-meet"\|meeting_start' jarvis-web/src/static/app.html` → `5` 이상
`grep -c 'id="controls"' jarvis-web/src/static/app.html` → `0`
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(SP2,jarvis-web): 하단 dock(음성 pill + 메뉴) + 미팅모드 항목 + 회의종료 이동"
```

---

## Task 3: 검증 + 배포

**Files:** (없음 — 검증·배포만)

- [ ] **Step 1: 전체 검증** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed; `cd jarvis-web && npm run typecheck` → 오류 없음.

- [ ] **Step 2: best-effort 통합 체크(회귀)** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s, "Ready") → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 기존 줄 모두 OK → `pkill -f "wrangler dev"`. 안 뜨면 스킵+사유.

- [ ] **Step 3: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 폰 홈 → 하단 dock 음성 pill(핸즈프리 그대로) → `+` → 메뉴 "미팅모드" → 회의 뷰 전환 + jarvis 회의 시작 → dock 에 🛑 회의 종료 → 탭 → 홈 복귀. (jarvis 재시작 필요 — meeting_start 반영.)

---

## Self-Review 결과

**Spec coverage:**
- 하단 고정 dock(음성 pill 감쌈) → Task 2 Step 1·3 ✓
- 홈 `+` 버튼 + 기능 메뉴(미팅모드) → Task 2 Step 1·3·5 ✓
- 회의 뷰: `+` 자리에 회의 종료 → Task 2 CSS 게이팅(meeting-stop dock 이동) ✓
- 메뉴 미팅모드 → meeting_start → jarvis 회의 시작 → Task 1 + Task 2 Step 5 ✓
- lockbar/컨텐츠 패딩 조정 → Task 2 Step 1·2 ✓
- 검증·배포·E2E → Task 3 ✓
- 비범위(소스 토글 SP3, 공개 뷰어 SP4) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `meeting_start` 일치 — 웹 `sendControl({kind:"meeting_start"})`(menu-meet) ↔ ControlReceiver 일반 포워딩 ↔ main `_on_remote_command` `"meeting_start"` → `start_meeting_setup()`. dock 요소 id(`dock`/`dock-plus`/`plus-menu`/`menu-meet`/`voice-toggle`/`meeting-stop`) 가 CSS·DOM·JS 에서 일관. `sendControl`/`getPw`/`showLogin` 기존 정의 재사용.

**핵심 위험:** (1) 상단 `#controls` 제거 — JS 참조 없음(meeting-stop 은 id 유지 dock 이동). (2) `#voice-toggle` 재스타일(fixed 원형 → dock pill)이지만 id·핸들러·active 펄스 유지. (3) `+` 메뉴는 document 클릭으로 닫히되 dock-plus 클릭은 stopPropagation 으로 즉시닫힘 방지. (4) setView 가 메뉴 닫아 회의 진입 시 잔류 방지. (5) dock 가림은 #chat/#log padding-bottom + lockbar bottom 상향으로 방지(수동 확인).
```
