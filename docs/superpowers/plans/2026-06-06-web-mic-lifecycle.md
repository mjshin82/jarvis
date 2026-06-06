# 웹 마이크 수명주기 캡슐화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `jarvis-web/src/static/app.html` 의 마이크 캡처를 단일 `mic` 객체(generation 가드)로 캡슐화하고 `shouldCapture` 파생 상태로 단일화해 desync·stale 교차종료를 제거한다.

**Architecture:** 의도 변수(view/voiceOn/micSource)만 사용자/서버 동작이 갱신하고, 캡처 시작/중단은 오직 `mic.apply()` 단일 진입점이 `shouldCapture()` 와 내부 `capturing` 을 비교해 수행한다. 모든 비동기 콜백은 generation 카운터로 자기 세대를 확인해 stale 콜백을 무력화한다.

**Tech Stack:** 바닐라 JS(인라인 `<script>` in app.html, IIFE), Cloudflare Worker(번들), 자동 JS 테스트 없음 → 검증은 JS 구문검사 + wrangler 타입체크 + 수동 E2E.

**스펙:** `docs/superpowers/specs/2026-06-06-web-mic-lifecycle-design.md`

---

## 검증 방법 (자동 테스트 부재 — 각 태스크 공통)

app.html 의 JS 는 문자열로 번들돼 빌드가 구문오류를 못 잡는다. 각 코드변경 태스크는 아래 두 게이트를 통과해야 한다:

**(A) 인라인 JS 구문검사** — `<script>` 본문을 `new Function` 으로 파싱(실행 안 함):
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const body=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(body);}catch(e){console.error('SYNTAX ERROR:',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
```
기대: `JS syntax OK`

**(B) 워커 타입/번들 체크**:
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npm run typecheck
```
기대: 에러 없음(종료코드 0). (app.html 은 문자열이라 TS 영향 없음 — 빌드 깨짐만 확인.)

---

## 현재 코드 기준점 (app.html, 변경 전)

- L218 `let micSource = "system";`  · L579 `let voiceOn = false;`
- L232 `let audioCtx = null, playHead = 0, keepAlive = null;` · `ensureAudio()`(L234) 가 audioCtx 생성
- L439 `let micWS = null, micNode = null, micStream = null, micOn = false, wakeLock = null;`
- L440 `downsample()`, L447 `floatToInt16()`, L455 `requestWakeLock()`, L458 visibilitychange
- L461 `loseMic()`, L466 `micStart()`, L492 `micStop()`
- L517 meeting-stop 핸들러, L522 mic-src-toggle 핸들러, L580 voice-toggle 핸들러
- handle: L323 `case "navigate": showView(ev.text); return;` · L393 `case "mic_source": ...`
- L219 `setView(v)` 가 `document.body.dataset.view` 설정 · L602 부근 init(`setView("home"); ... connect()`)
- 헬퍼: `getPw()`(L204), `name`(L200), `ADMIN_KEY`(L198), `$()`, `showLogin()`

(라인번호는 근사 — 편집 전 반드시 파일을 읽어 현재 위치를 확인할 것.)

---

## Task 1: `createMic` 팩토리 + `curView` + `onMicLost` + mic 인스턴스 추가 (기존 코드 유지)

이 태스크는 새 mic 객체를 **추가만** 한다(아직 미사용). 기존 micStart/micStop/loseMic 는 그대로 둔다.

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: `curView()` 헬퍼 추가**

`setView`/`showView` 정의 근처(파일에서 `function showView(` 바로 다음 줄)에 추가:
```javascript
  function curView() { return document.body.dataset.view === "meeting" ? "meeting" : "home"; }
```

- [ ] **Step 2: `createMic` 팩토리 추가**

`floatToInt16` 함수 정의 끝(`}` 다음 줄) 바로 아래에 추가:
```javascript
  // ---- 마이크 캡처 객체: 단일 진입점 apply() + generation 가드 ----
  function createMic(deps) {
    let ws = null, node = null, stream = null, wakeLock = null;
    let gen = 0, capturing = false;

    function _teardownAudio() {
      if (node) { try { node.disconnect(); } catch {} node.onaudioprocess = null; node = null; }
      if (stream) { try { stream.getTracks().forEach((t) => t.stop()); } catch {} stream = null; }
    }
    function _teardownSocket(sendStop) {
      if (ws) {
        if (sendStop) { try { if (ws.readyState === 1) ws.send(JSON.stringify({ kind: "mic_stop" })); } catch {} }
        try { ws.close(); } catch {}
        ws = null;
      }
    }
    function _releaseWakeLock() {
      if (wakeLock) { try { wakeLock.release(); } catch {} wakeLock = null; }
    }
    async function _acquireWakeLock(myGen) {
      try {
        if ("wakeLock" in navigator) {
          const wl = await navigator.wakeLock.request("screen");
          if (myGen !== gen) { try { wl.release(); } catch {} return; }
          wakeLock = wl;
          wakeLock.addEventListener("release", () => { wakeLock = null; });
        }
      } catch {}
    }

    async function _start() {
      const myGen = ++gen;          // 새 세대 — 이전 콜백 무효화
      capturing = true;
      const pw = deps.getPw();
      if (!pw) { deps.onLost("auth"); return; }
      _teardownSocket(false);       // close-before-open
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${deps.host}/mic/${encodeURIComponent(deps.name)}?token=${encodeURIComponent(pw)}`);
      ws.binaryType = "arraybuffer";
      let opened = false;
      ws.onopen = () => { if (myGen !== gen) return; opened = true; ws.send(JSON.stringify({ kind: "mic_start" })); };
      ws.onmessage = (e) => { if (myGen !== gen) return; try { if (JSON.parse(e.data).kind === "kicked") deps.onLost("kicked"); } catch {} };
      ws.onclose = (e) => {
        if (myGen !== gen) return;  // stale close 무시(교차종료 차단)
        if (e.code === 1008 || (!opened && e.code === 1006)) deps.onLost("auth");
        else deps.onLost("closed");
      };
      let s;
      try {
        s = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      } catch (err) {
        if (myGen === gen) deps.onLost("mic-permission");
        return;
      }
      if (myGen !== gen) { try { s.getTracks().forEach((t) => t.stop()); } catch {} return; }
      stream = s;
      deps.ensureAudio();
      const ctx = deps.getAudioCtx();
      const srcNode = ctx.createMediaStreamSource(stream);
      node = ctx.createScriptProcessor(4096, 1, 1);
      srcNode.connect(node); node.connect(ctx.destination);
      await _acquireWakeLock(myGen);
      node.onaudioprocess = (ev) => {
        if (myGen !== gen) return;
        if (!ws || ws.readyState !== 1) return;
        ws.send(deps.floatToInt16(deps.downsample(ev.inputBuffer.getChannelData(0), ctx.sampleRate)).buffer);
      };
    }

    function _stop() {
      gen++;                        // 진행 중 모든 콜백(자기 onclose 포함) 무효화
      capturing = false;
      _teardownAudio();
      _teardownSocket(true);
      _releaseWakeLock();
      const ctx = deps.getAudioCtx();
      if (ctx && ctx.state === "suspended") ctx.resume();
    }

    function apply() {
      const want = deps.shouldCapture();
      if (want && !capturing) _start();
      else if (!want && capturing) _stop();
    }

    document.addEventListener("visibilitychange", () => {
      if (capturing && wakeLock === null && document.visibilityState === "visible") _acquireWakeLock(gen);
    });

    return { apply, isCapturing: () => capturing };
  }
```

- [ ] **Step 3: `onMicLost` + mic 인스턴스 추가**

init 직전(파일에서 `setView("home");` 줄 바로 위)에 추가:
```javascript
  function onMicLost(reason) {
    if (curView() === "home") { voiceOn = false; $("voice-toggle").classList.remove("active"); }
    else { micSource = "system"; $("mic-src-toggle").classList.remove("active"); }
    mic.apply();
    if (reason === "auth") { localStorage.removeItem(ADMIN_KEY); showLogin(); }
    else if (reason === "mic-permission") alert("마이크 권한 실패 — 권한을 허용해주세요.");
  }

  const mic = createMic({
    getPw, host: location.host, name,
    ensureAudio, getAudioCtx: () => audioCtx,
    floatToInt16, downsample,
    shouldCapture: () => (curView() === "home" && voiceOn) || (curView() === "meeting" && micSource === "remote"),
    onLost: onMicLost,
  });
```

- [ ] **Step 4: 검증** — 위 "(A) JS 구문검사" + "(B) 타입체크" 둘 다 통과(`JS syntax OK`, typecheck 에러 없음).

- [ ] **Step 5: 커밋**
```bash
git add jarvis-web/src/static/app.html
git commit -m "feat(web-mic): createMic 객체 + curView/onMicLost/mic 인스턴스 추가(미사용)"
```

---

## Task 2: 핸들러를 의도+apply 로 재배선 (voice-toggle / mic-src-toggle / navigate / mic_source)

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: voice-toggle 핸들러 교체**

현재(L580 부근):
```javascript
  $("voice-toggle").addEventListener("click", async () => {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    ensureAudio();
    voiceOn = !voiceOn;
    $("voice-toggle").classList.toggle("active", voiceOn);
    if (voiceOn) {
      if (!micOn) {
        try { await micStart(); micOn = true; }
        catch (e) {
          alert("마이크 권한 실패: " + e.message);
          voiceOn = false; $("voice-toggle").classList.remove("active"); return;
        }
      }
      sendControl({ kind: "listen_start" });
    } else {
      sendControl({ kind: "listen_stop" });
      if (micOn) { micStop(); micOn = false; }
    }
  });
```
교체 후:
```javascript
  $("voice-toggle").addEventListener("click", () => {
    const pw = getPw();
    if (!pw) { showLogin(); return; }
    ensureAudio();
    voiceOn = !voiceOn;
    $("voice-toggle").classList.toggle("active", voiceOn);
    sendControl({ kind: voiceOn ? "listen_start" : "listen_stop" });
    mic.apply();
  });
```

- [ ] **Step 2: mic-src-toggle 핸들러 교체**

현재(L522 부근):
```javascript
  $("mic-src-toggle").addEventListener("click", async () => {
    if (!getPw()) { showLogin(); return; }
    ensureAudio();
    if (micSource === "remote") {
      sendControl({ kind: "mic_system" });
      if (micOn) { micStop(); micOn = false; }
      micSource = "system";
      $("mic-src-toggle").classList.remove("active");
    } else {
      try { if (!micOn) { await micStart(); micOn = true; } }
      catch (e) { alert("마이크 권한 실패: " + e.message); return; }
      sendControl({ kind: "mic_phone" });
      micSource = "remote";
      $("mic-src-toggle").classList.add("active");
    }
  });
```
교체 후(낙관적 + apply):
```javascript
  $("mic-src-toggle").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    ensureAudio();
    micSource = (micSource === "remote") ? "system" : "remote";
    $("mic-src-toggle").classList.toggle("active", micSource === "remote");
    sendControl({ kind: micSource === "remote" ? "mic_phone" : "mic_system" });
    mic.apply();
  });
```

- [ ] **Step 3: navigate handle 케이스 교체**

현재(L323): `case "navigate": showView(ev.text); return;`
교체 후:
```javascript
      case "navigate": showView(ev.text); mic.apply(); return;
```

- [ ] **Step 4: mic_source handle 케이스 교체 (서버 권위 재조정)**

현재(L393 부근):
```javascript
      case "mic_source":
        micSource = ev.source || "system";
        $("mic-src-toggle").classList.toggle("active", micSource === "remote");   // 붉은=폰
        return;
```
교체 후:
```javascript
      case "mic_source":
        micSource = ev.source || "system";
        $("mic-src-toggle").classList.toggle("active", micSource === "remote");   // 붉은=폰
        mic.apply();   // 낙관적 클릭과 어긋났으면 서버값으로 수렴
        return;
```

- [ ] **Step 5: 검증** — "(A) JS 구문검사" + "(B) 타입체크" 통과. 추가로 핸들러에 `micStart`/`micStop`/`micOn` 참조가 남지 않았는지 확인:
```bash
grep -n "micStart\|micStop\|micOn" jarvis-web/src/static/app.html
```
기대: 매치는 아직 **정의부(loseMic/micStart/micStop 함수 본문, L439 의 micOn 선언)** 에만 남아있고, 핸들러(voice-toggle/mic-src-toggle)에는 없음. (정의부는 Task 3 에서 제거.)

- [ ] **Step 6: 커밋**
```bash
git add jarvis-web/src/static/app.html
git commit -m "refactor(web-mic): 토글/navigate/mic_source 를 의도+mic.apply() 로 재배선"
```

---

## Task 3: 죽은 옛 코드 제거 (loseMic/micStart/micStop/micOn/옛 wakeLock)

**Files:** Modify `jarvis-web/src/static/app.html`

- [ ] **Step 1: 옛 함수·변수 삭제**

다음을 통째로 삭제한다:
- `loseMic` 함수 전체 (L461~465)
- `micStart` 함수 전체 (L466~491)
- `micStop` 함수 전체 (L492~500)
- `requestWakeLock` 함수 전체 (L455~457)
- 옛 visibilitychange 리스너 (L458~460): `document.addEventListener("visibilitychange", () => { if (micOn && wakeLock === null ...) requestWakeLock(); });`
- L439 의 mic 캡처 변수 선언 라인 `let micWS = null, micNode = null, micStream = null, micOn = false, wakeLock = null;` 전체 삭제 (모두 mic 객체 내부로 이전됨).

유지: `downsample`(L440), `floatToInt16`(L447), `sendControl`(L502), mic 캡처 주석 라인 `// ---- mic-take ... ----`(L438) 은 의미가 바뀌었으니 삭제 또는 갱신(`// ---- (mic 캡처는 createMic 객체로 이전) ----`).

- [ ] **Step 2: 잔존 참조 0 확인**
```bash
grep -n "micStart\|micStop\|loseMic\|micOn\|micWS\|micNode\|micStream\|requestWakeLock\b" jarvis-web/src/static/app.html || echo "CLEAN"
```
기대: `CLEAN` (mic 객체 내부의 지역변수 `ws/node/stream/wakeLock` 은 이 패턴에 안 걸림). 만약 `wakeLock` 매치가 mic 객체 내부(createMic) 라면 OK — 모듈 전역 `wakeLock` 참조만 없으면 됨. grep 결과를 보고 모듈 스코프에 남은 게 없는지 확인.

- [ ] **Step 3: 검증** — "(A) JS 구문검사" + "(B) 타입체크" 통과.

- [ ] **Step 4: 커밋**
```bash
git add jarvis-web/src/static/app.html
git commit -m "refactor(web-mic): 옛 micStart/micStop/loseMic/micOn 제거(mic 객체로 일원화)"
```

---

## Task 4: 빌드 검증 + 수동 E2E 체크리스트

**Files:** (변경 없음 — 검증)

- [ ] **Step 1: 최종 정적 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const body=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(body);}catch(e){console.error('SYNTAX ERROR:',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
grep -n "micStart\|micStop\|loseMic\|micOn\|micWS" src/static/app.html || echo "CLEAN"
```
기대: `JS syntax OK`, typecheck 0, `CLEAN`.

- [ ] **Step 2: 배포 (사용자 확인 후)**
```bash
cd /Users/oracle/Documents/concode/jarvis/jarvis-web && npx wrangler deploy
```

- [ ] **Step 3: 수동 E2E 체크리스트 (폰/iOS 실기기)**
사용자에게 실행 요청:
1. 홈 음성 ON→말→응답→OFF — 캡처·버튼 상태 일치
2. 빠른 더블탭 ON/OFF/ON — 최종 상태 정확, 끊김/유령소켓 없음
3. 회의 입장→폰 토글(입력됨)→시스템 토글(중단)
4. **회의 중 폰 입력→회의 종료→홈에서 본체로 안 새고 캡처 자동 중단**
5. 두 기기 mic 경합(kick) — 뺏긴 쪽 off + 알림
6. 기내모드 토글(네트워크 끊김) — 인지 + 의도 off, 무한재시도 X
7. 인증 만료 → 로그인 / 마이크 권한 거부 → 알림
8. iOS 화면잠금→해제 — wakeLock 재획득, 캡처 유지

---

## 비고
- 서버(`meeting_do.ts`)·파이썬 무변경. 파이썬 테스트 95개 영향 없음.
- 자동 JS 테스트는 도입 안 함(스펙 비범위) — 검증은 구문검사+타입체크+수동 E2E.
- 배포는 사용자 확인 후 `wrangler deploy`. origin push 는 사용자가 직접.
