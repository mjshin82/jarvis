# 핸즈프리 음성 버튼 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 홈 채팅 우하단 음성 버튼을 탭하면 폰 마이크 ON + 호출어 없이 즉시 청취(타임아웃 없음), 다시 탭하면 정지 + 마이크 OFF.

**Architecture:** 기존 `/control` 역방향 채널로 `listen_start`/`listen_stop` 을 전송. jarvis 는 `hands_free` 플래그로 `listen_timeout` 을 무효화하고 응답 후에도 계속 재청취. `ControlReceiver` 는 임의 명령을 `on_command` 으로 일반 포워딩.

**Tech Stack:** Python 3.11 (pytest) · 바닐라 JS/HTML/CSS.

전제: `cd /Users/oracle/Documents/concode/jarvis`. pytest: `.venv/bin/python -m pytest`.

---

## Task 1: control_receiver.py — 일반 포워딩 + 테스트 갱신

**Files:** Modify `control_receiver.py`, Modify `tests/test_control_receiver.py`

- [ ] **Step 1: 테스트 갱신** — `tests/test_control_receiver.py` 의 `test_other_kinds_ignored` 함수를 아래 두 함수로 교체(파일의 그 함수만 교체, `test_meeting_stop_dispatches` 와 `_rx` 헬퍼는 유지):

현재:
```python
def test_other_kinds_ignored():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"something_else"}'))
    asyncio.run(rx._handle_message("not json at all"))
    asyncio.run(rx._handle_message('{"no":"kind"}'))
    assert calls == []
```
교체:
```python
def test_known_commands_dispatch():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"listen_start"}'))
    asyncio.run(rx._handle_message('{"kind":"listen_stop"}'))
    asyncio.run(rx._handle_message('{"kind":"meeting_stop"}'))
    assert calls == ["listen_start", "listen_stop", "meeting_stop"]


def test_non_commands_ignored():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"no_receiver"}'))   # 로그만, 명령 아님
    asyncio.run(rx._handle_message("not json at all"))
    asyncio.run(rx._handle_message('{"no":"kind"}'))
    assert calls == []
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_control_receiver.py -v` → `test_known_commands_dispatch` FAIL (현재 listen_start/listen_stop 는 on_command 호출 안 됨).

- [ ] **Step 3: 구현** — `control_receiver.py` 의 `_handle_message` 끝부분. 현재:
```python
        kind = msg.get("kind")
        if kind == "meeting_stop":
            await self.on_command("meeting_stop")
        elif kind == "no_receiver":
            self.on_log("[control] relay: 수신자 없음 통지")
```
교체:
```python
        kind = msg.get("kind")
        if kind == "no_receiver":
            self.on_log("[control] relay: 수신자 없음 통지")
        elif kind:
            await self.on_command(kind)   # meeting_stop·listen_start·listen_stop 등 포워딩
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_control_receiver.py -v` (3 pass), `.venv/bin/python -m pytest tests/ -q` (0 failed).

- [ ] **Step 5: 커밋**
```bash
git add control_receiver.py tests/test_control_receiver.py
git commit -m "feat: ControlReceiver 일반 명령 포워딩(listen_start/stop 등) + 테스트 갱신"
```

---

## Task 2: main.py — hands_free 청취

**Files:** Modify `main.py`

- [ ] **Step 1: hands_free 플래그 선언** — 현재(107-109행):
```python
    state = "WAITING_WAKE"
    response: asyncio.Task | None = None   # 진행 중 응답 흐름 (텍스트 또는 음성)
    watchdog: asyncio.Task | None = None   # LISTENING 타임아웃
```
교체:
```python
    state = "WAITING_WAKE"
    response: asyncio.Task | None = None   # 진행 중 응답 흐름 (텍스트 또는 음성)
    watchdog: asyncio.Task | None = None   # LISTENING 타임아웃
    hands_free = False                     # 웹 음성버튼 핸즈프리 — 타임아웃 무효 + 계속 청취
```

- [ ] **Step 2: _on_remote_command 확장** — 현재(97-99행):
```python
        async def _on_remote_command(kind):
            if kind == "meeting_stop":
                await stop_meeting()
```
교체:
```python
        async def _on_remote_command(kind):
            nonlocal hands_free, response, watchdog
            if kind == "meeting_stop":
                await stop_meeting()
            elif kind == "listen_start":
                hands_free = True
                await trigger_wake()   # 호출어 없이 즉시 청취
            elif kind == "listen_stop":
                hands_free = False
                await cancel(response); response = None
                if watchdog is not None and not watchdog.done():
                    watchdog.cancel()
                watchdog = None
                idle()
```
(`_on_remote_command` 는 정의 시점이 trigger_wake/cancel/idle 보다 위지만, 호출은 control_rx 수신 시점이라 클로저로 모두 존재. `nonlocal hands_free` 는 Step 1 의 main 지역변수 hands_free 를 가리킴.)

- [ ] **Step 3: listen_timeout 무효화** — 현재 `listen_timeout` 의 번역 체크:
```python
        # 번역 모드는 사용자가 /stop 으로만 빠져나가므로 타임아웃 무효
        if MODE.is_translate():
            return
        if state == "LISTENING":
```
교체:
```python
        # 번역 모드는 사용자가 /stop 으로만 빠져나가므로 타임아웃 무효
        if MODE.is_translate():
            return
        if hands_free:
            return   # 핸즈프리 — 타임아웃 없이 계속 청취
        if state == "LISTENING":
```

- [ ] **Step 4: 응답 후 재청취 규칙** — `_respond_voice` 와 `respond_flow_audio` 양쪽에 동일한 꼬리가 있다. 두 곳 모두 교체(replace_all). 현재:
```python
        if config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
            idle()
```
교체:
```python
        if hands_free or config.FOLLOW_UP:
            await enter_listening(cue=True)
        else:
            idle()
```

- [ ] **Step 5: 검증**

Run: `.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); import main; print('ok')"` → `ok`
Run: `grep -c 'hands_free' main.py` → `6` 이상 (선언1 + nonlocal1 + listen_start1 + listen_stop1 + timeout1 + 재청취2 = 7 매칭 줄)
Run: `.venv/bin/python -m pytest tests/ -q` → 0 failed

- [ ] **Step 6: 커밋**
```bash
git add main.py
git commit -m "feat: main — 핸즈프리 청취(listen_start/stop, 타임아웃 무효, 계속 재청취)"
```

---

## Task 3: app.html — 음성 토글 버튼

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: 버튼 CSS** — `<style>` 의 meeting-stop 규칙(현재 46-47행):
```css
  #meeting-stop { display: none; background: #dc2626; }
  body[data-view="meeting"] #meeting-stop { display: inline-block; }
```
다음에 추가:
```css
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

- [ ] **Step 2: 버튼 DOM** — 현재 home-view 블록:
```html
  <div id="home-view">
    <main id="chat"></main>
  </div>
```
교체(파형 SVG 버튼 추가 — fixed 라 위치 무관하지만 home-view 다음에 둠):
```html
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
```

- [ ] **Step 3: sendControl 헬퍼 + meeting-stop 리팩터** — 현재 meeting-stop 핸들러:
```js
  // ---- 회의 종료 (웹 → control 채널, one-shot) ----
  $("meeting-stop").addEventListener("click", () => {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    try {
      const cws = new WebSocket(`${proto}//${location.host}/control/${encodeURIComponent(name)}?token=${encodeURIComponent(pw)}`);
      cws.onopen = () => {
        try { cws.send(JSON.stringify({ kind: "meeting_stop" })); }
        finally { setTimeout(() => { try { cws.close(); } catch {} }, 200); }
      };
      cws.onerror = () => {};
    } catch {}
  });
```
교체:
```js
  // ---- 제어 채널(웹 → jarvis) one-shot 전송 ----
  function sendControl(obj) {
    const pw = getPw();
    if (!pw) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    try {
      const cws = new WebSocket(`${proto}//${location.host}/control/${encodeURIComponent(name)}?token=${encodeURIComponent(pw)}`);
      cws.onopen = () => {
        try { cws.send(JSON.stringify(obj)); }
        finally { setTimeout(() => { try { cws.close(); } catch {} }, 200); }
      };
      cws.onerror = () => {};
    } catch {}
  }

  // ---- 회의 종료 (웹 → control) ----
  $("meeting-stop").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    sendControl({ kind: "meeting_stop" });
  });

  // ---- 음성 토글 (핸즈프리: 마이크 ON + 즉시 청취, 타임아웃 없음) ----
  let voiceOn = false;
  function syncMicUI() {
    $("mic-toggle").textContent = micOn ? "🎙️ 마이크 끄기" : "🎙️ 마이크 켜기";
    $("mic-toggle").classList.toggle("off", micOn);
  }
  $("voice-toggle").addEventListener("click", async () => {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    ensureAudio();
    voiceOn = !voiceOn;
    $("voice-toggle").classList.toggle("active", voiceOn);
    if (voiceOn) {
      if (!micOn) {
        try { await micStart(); micOn = true; syncMicUI(); }
        catch (e) {
          alert("마이크 권한 실패: " + e.message);
          voiceOn = false; $("voice-toggle").classList.remove("active"); return;
        }
      }
      sendControl({ kind: "listen_start" });
    } else {
      sendControl({ kind: "listen_stop" });
      if (micOn) { micStop(); micOn = false; syncMicUI(); }
    }
  });
```
(`micOn`/`micStart`/`micStop`/`name`/`getPw`/`ensureAudio`/`showLogin` 은 모두 같은 IIFE 안에 이미 정의됨.)

- [ ] **Step 4: 검증 + 커밋**

Run: `cd /Users/oracle/Documents/concode/jarvis`
`awk '/<script>/{f=1;next} /<\/script>/{f=0} f' jarvis-web/src/static/app.html > /tmp/appv.js && node --check /tmp/appv.js && echo "JS OK"` → `JS OK`
`grep -c 'voice-toggle\|listen_start\|listen_stop\|sendControl' jarvis-web/src/static/app.html` → `8` 이상
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(jarvis-web): 핸즈프리 음성 토글 버튼(우하단) + sendControl 헬퍼"
```

---

## Task 4: 검증 + 배포

**Files:** (없음 — 검증·배포만. 워커 로직 변경 없음: `/control` 라우트는 이미 존재.)

- [ ] **Step 1: 전체 검증** — `cd /Users/oracle/Documents/concode/jarvis && .venv/bin/python -m pytest tests/ -q` → 0 failed; `cd jarvis-web && npm run typecheck` → 오류 없음.

- [ ] **Step 2: best-effort 통합 체크(회귀)** — `cd jarvis-web && npx wrangler dev --port 8787 > /tmp/jw.log 2>&1 &` (10s, "Ready") → `RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs` → 기존 줄 모두 OK(특히 "control forward: OK") → `pkill -f "wrangler dev"`. 안 뜨면 스킵+사유.

- [ ] **Step 3: 배포 (머지 후 컨트롤러 — 서브에이전트 건너뜀)** — `cd jarvis-web && npm run deploy`. 수동 E2E: 폰 홈 → 우하단 파형 버튼 탭 → 마이크 ON + jarvis 가 호출어 없이 청취(가만 둬도 타임아웃 없이 대기) → 말하면 조합중→응답 후 계속 청취 → 다시 탭 → 정지 + 마이크 OFF + 호출어 대기 복귀. jarvis **재시작** 필요(control_rx/hands_free 반영).

---

## Self-Review 결과

**Spec coverage:**
- 우하단 파형 버튼(홈 전용, 활성 펄스) → Task 3 ✓
- ON = 마이크 ON + listen_start, OFF = listen_stop + 마이크 OFF → Task 3 ✓
- jarvis hands_free(listen_start/stop, 타임아웃 무효, 계속 재청취) → Task 2 ✓
- ControlReceiver 일반 포워딩 + 테스트 갱신 → Task 1 ✓
- sendControl 헬퍼로 meeting_stop 재사용(DRY) → Task 3 ✓
- 검증·배포·E2E → Task 4 ✓
- 비범위(unload 통지, 회의 동시) → 미구현 ✓

**Placeholder scan:** 모든 코드 step 완전. 빈칸 없음.

**Type/이름 consistency:** `listen_start`/`listen_stop` 문자열 4계층 일치 — 웹 `sendControl({kind:"listen_start"|"listen_stop"})` ↔ DO 통과 ↔ `ControlReceiver` 일반 포워딩 → `on_command(kind)` ↔ main `_on_remote_command` 의 `"listen_start"`/`"listen_stop"`. `hands_free`(Step1 선언 → nonlocal/timeout/재청취 일관). 웹 `voiceOn`/`syncMicUI`/`sendControl`/`micOn`/`micStart`/`micStop` 일관.

**핵심 위험:** (1) `_on_remote_command` 가 trigger_wake/cancel/idle/hands_free 를 클로저로 참조 — 정의는 위지만 호출은 런타임이라 안전. (2) listen_stop 이 진행 응답을 취소해 깔끔히 WAITING_WAKE 복귀. (3) 재청취 규칙이 hands_free 우선 → FOLLOW_UP 설정 무관하게 핸즈프리 지속. (4) 웹 버튼이 micOn 공유로 mic-toggle 와 상태 동기화(syncMicUI). (5) recognizer 없어도(폴백) hands_free 가 VAD 경로 타임아웃만 무효화하므로 동작.
```
