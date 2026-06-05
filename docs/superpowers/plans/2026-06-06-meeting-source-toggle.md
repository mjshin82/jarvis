# SP3 — meeting 음성 소스 토글 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 모드 dock 에 입력 소스 토글 pill(본체/폰)을 추가해, 탭으로 jarvis 입력 소스를 전환한다.

**Architecture:** 토글이 control 채널로 `mic_system`/`mic_phone` 전송 → jarvis `MicRouter.set_override("local"|"remote")`. 소스 변경 시 기존 `mic_source` 이벤트가 웹으로 돌아와 토글 라벨을 갱신.

**Tech Stack:** 바닐라 JS/HTML/CSS · Python(main.py 2분기).

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`.

---

## Task 1: main.py — mic_system/mic_phone control 명령

**Files:** Modify `main.py`

- [ ] **Step 1: 분기 추가** — 현재:
```python
        async def _on_remote_command(kind):
            nonlocal hands_free, response, watchdog
            if kind == "meeting_stop":
                await stop_meeting()
            elif kind == "meeting_start":
                await start_meeting_setup()   # 성공 시 _begin_meeting 이 navigate(meeting) 발행
            elif kind == "listen_start":
```
교체(meeting_start 다음에 mic 분기 추가):
```python
        async def _on_remote_command(kind):
            nonlocal hands_free, response, watchdog
            if kind == "meeting_stop":
                await stop_meeting()
            elif kind == "meeting_start":
                await start_meeting_setup()   # 성공 시 _begin_meeting 이 navigate(meeting) 발행
            elif kind == "mic_system":
                mic.router.set_override("local")    # 입력 소스 → jarvis 본체
            elif kind == "mic_phone":
                mic.router.set_override("remote")   # 입력 소스 → 웹(폰)
            elif kind == "listen_start":
```

- [ ] **Step 2: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 3: 커밋**
```bash
git add main.py
git commit -m "feat(SP3): main — mic_system/mic_phone → MicRouter.set_override"
```

---

## Task 2: app.html — 소스 토글 pill

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: CSS — meeting-stop 폭 + 토글 스타일/게이팅** — 현재:
```css
  #meeting-stop { flex: 1; height: 48px; border-radius: 24px; background: #dc2626; }
  body[data-view="home"] #meeting-stop { display: none; }
  body[data-view="meeting"] #voice-toggle,
  body[data-view="meeting"] #dock-plus { display: none; }
```
교체:
```css
  #meeting-stop { height: 48px; border-radius: 24px; background: #dc2626; }
  #mic-src-toggle { flex: 1; height: 48px; border-radius: 24px; background: #111; color: #fff; }
  body[data-view="home"] #meeting-stop,
  body[data-view="home"] #mic-src-toggle { display: none; }
  body[data-view="meeting"] #voice-toggle,
  body[data-view="meeting"] #dock-plus { display: none; }
```

- [ ] **Step 2: DOM — dock 에 토글 버튼 추가** — 현재:
```html
    <button id="dock-plus" aria-label="메뉴">+</button>
    <button id="meeting-stop">🛑 회의 종료</button>
```
교체(meeting-stop 앞에 토글 추가):
```html
    <button id="dock-plus" aria-label="메뉴">+</button>
    <button id="mic-src-toggle">🎙 입력: 시스템</button>
    <button id="meeting-stop">🛑 회의 종료</button>
```

- [ ] **Step 3: micSource 상태 변수** — 현재:
```js
  let meetingTitle = "🎤 Meeting";   // applyMeta 가 갱신; 제목은 뷰에 따라 표시
```
교체:
```js
  let meetingTitle = "🎤 Meeting";   // applyMeta 가 갱신; 제목은 뷰에 따라 표시
  let micSource = "system";          // 회의 입력 소스 (system|remote) — 토글 라벨용
```

- [ ] **Step 4: mic_source 핸들러 — 라벨 갱신** — 현재:
```js
      case "mic_source": return;   // 소스 배지 제거 — 무시
```
교체:
```js
      case "mic_source":
        micSource = ev.source || "system";
        $("mic-src-toggle").textContent = micSource === "remote" ? "🎙 입력: 폰" : "🎙 입력: 시스템";
        return;
```

- [ ] **Step 5: 토글 클릭 핸들러** — 기존 meeting-stop 핸들러(아래) 다음에 삽입:
```js
  // ---- 회의 종료 (웹 → control) ----
  $("meeting-stop").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    sendControl({ kind: "meeting_stop" });
  });
```
삽입:
```js
  // ---- 회의 입력 소스 토글 (본체 ⇄ 폰) ----
  $("mic-src-toggle").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    sendControl({ kind: micSource === "remote" ? "mic_system" : "mic_phone" });
  });
```

- [ ] **Step 6: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/appst.js && node --check /tmp/appst.js && echo "JS OK"` → `JS OK`
`grep -c 'mic-src-toggle\|micSource\|mic_system\|mic_phone' jarvis-web/src/static/app.html` → `6` 이상
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(SP3,jarvis-web): 회의 dock 입력 소스 토글(본체/폰) pill"
```

---

## Task 3: 검증 + 배포

**Files:** (없음)

- [ ] **Step 1: 전체 검증** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed; `cd jarvis-web && npm run typecheck` → 오류 없음.

- [ ] **Step 2: best-effort 통합 체크(회귀)** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s, "Ready") → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 기존 줄 모두 OK → `pkill -f "wrangler dev"`. 안 뜨면 스킵+사유.

- [ ] **Step 3: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 회의 진입 → dock 좌측 "🎙 입력: 시스템" → 탭 → "폰" 으로 바뀌고 jarvis 콘솔 "입력 소스 → 원격(폰)" → 다시 탭 → 시스템. (jarvis 재시작 필요.)

---

## Self-Review 결과

**Spec coverage:**
- 회의 dock 좌측 소스 토글 pill(본체/폰) → Task 2 ✓
- 탭 → set_override 전환 → Task 1 + Task 2 Step 5 ✓
- mic_source 이벤트로 라벨 동기화(헤더 배지 부활 X) → Task 2 Step 3·4 ✓
- meeting-stop 우측 auto 폭 → Task 2 Step 1·2 ✓
- 검증·배포 → Task 3 ✓
- 비범위(공개 뷰어 SP4, auto UI) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `mic_system`/`mic_phone` 일치 — 웹 `sendControl({kind:...})` ↔ ControlReceiver 일반 포워딩 ↔ main `_on_remote_command` ↔ `set_override("local"|"remote")`. `mic_source` 이벤트 source 값 `"system"|"remote"`(jarvis notify_source 가 local→system 정규화) ↔ 웹 `micSource` 비교(`"remote"`). dock id `mic-src-toggle` CSS·DOM·JS 일관.

**핵심 위험:** (1) 소스 값 표기 — jarvis 는 web 계약상 "system"/"remote" 발행(local→system), 웹은 "remote" 만 폰으로 판별 → 일관. (2) meeting-stop flex:1 제거로 우측 auto, 토글이 flex:1 좌측 — 회의 dock 2버튼 레이아웃. (3) set_override 가 auto 끔 — 회의 중 자동전환 방지(의도). (4) 토글은 회의 뷰에서만 노출(홈 게이팅).
```
