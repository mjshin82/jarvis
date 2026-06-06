# 회의 메타(타이틀 + 워드북) 입력 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의 시작 시 타이틀·워드북을 입력(콘솔 대화형 / 웹 폼 / 기본)받아 워드북을 Gladia custom_vocabulary·RealtimeSTT initial_prompt 로 STT 에 주입하고, 타이틀을 웹 헤더에 표시한다.

**Architecture:** MeetingMeta 에 title/vocabulary 추가, MeetingSetup 2단계 부활(콘솔), 웹 폼이 meta 를 control 로 전달, `controller.start_meeting(meta, interactive)` 가 경로별 분기. STT backend 가 vocab 사용. 타이틀은 `meeting_title` owner 이벤트로 웹 동기화.

**Tech Stack:** Python 3.11 + pytest; TS Worker(`npm run typecheck`); 웹 인라인 JS(`node --check`).

**스펙:** `docs/superpowers/specs/2026-06-06-meeting-meta-wordbook-design.md`

---

## Task 1: MeetingMeta + MeetingSetup 대화형 (live_translate.py)

**Files:** Modify `live_translate.py`; Test `tests/test_meeting_session.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_session.py` 끝에

```python
def test_setup_defaults():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    assert s.meta.title == "회의"
    assert s.meta.vocabulary == ["Jarvis", "민준"]
    assert not s.done


def test_setup_submit_title_and_vocab():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit("주간회의")
    s.submit("신명진, 콘코드, Jarvis")
    assert s.done
    assert s.meta.title == "주간회의"
    assert s.meta.vocabulary == ["신명진", "콘코드", "Jarvis"]


def test_setup_empty_keeps_defaults():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit("")
    s.submit("")
    assert s.meta.title == "회의"
    assert s.meta.vocabulary == ["Jarvis", "민준"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: FAIL (MeetingMeta 에 title/vocabulary 없음, MeetingSetup steps 없음)

- [ ] **Step 3: 구현** — `live_translate.py`

(a) `MeetingMeta` 에 필드 추가(다른 필드 뒤, `key` 프로퍼티 앞). `field` 는 이미 import 됨:
```python
    title: str = ""
    vocabulary: list = field(default_factory=list)   # STT 보강 단어
```
(b) `_META_STEPS = ()` 를 교체:
```python
_META_STEPS = (
    ("title", "회의 제목을 입력하세요 (Enter=기본)"),
    ("vocabulary", "워드북 — 쉼표로 구분 (Enter=기본: Jarvis, 이름)"),
)
```
(c) `MeetingSetup` 의 `__init__` 과 `submit` 교체(`done`/`prompt` 유지):
```python
    def __init__(self, default_my_name: str = "Concode"):
        self._default_vocab = ["Jarvis", default_my_name]
        self.meta = MeetingMeta(my_name=default_my_name, title="회의",
                                vocabulary=list(self._default_vocab))
        self.step_index = 0

    def submit(self, value: str) -> None:
        """현재 단계 답 저장 후 다음 단계로. title/vocabulary 명시 처리(빈 입력→기본)."""
        key, _ = _META_STEPS[self.step_index]
        v = value.strip()
        if key == "title":
            self.meta.title = v or "회의"
        elif key == "vocabulary":
            if v:
                self.meta.vocabulary = [w.strip() for w in v.split(",") if w.strip()]
            # 빈 입력이면 기본 vocab 유지
        self.step_index += 1
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**
```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "feat(meeting): MeetingMeta title/vocabulary + MeetingSetup 2단계 대화형"
```

---

## Task 2: 워드북 → STT (gladia_stt.py + live_translate.py)

**Files:** Modify `gladia_stt.py`, `live_translate.py`; Test `tests/test_gladia_stt.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_gladia_stt.py` 끝에

```python
def test_config_custom_vocabulary():
    from gladia_stt import GladiaSTT
    g = GladiaSTT("k", languages=("ko", "en"), on_partial=lambda t: None,
                  on_final=lambda t: None, vocabulary=["신명진"])
    cfg = g._config()
    vocab = cfg["realtime_processing"]["custom_vocabulary_config"]["vocabulary"]
    assert vocab[0]["value"] == "신명진"
    assert vocab[0]["language"] == "ko"


def test_config_no_vocabulary_omits_realtime_processing():
    from gladia_stt import GladiaSTT
    g = GladiaSTT("k", on_partial=lambda t: None, on_final=lambda t: None)
    assert "realtime_processing" not in g._config()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_gladia_stt.py -q`
Expected: FAIL (vocabulary 인자/키 없음)

- [ ] **Step 3: gladia_stt.py 구현**

(a) `__init__` 시그니처에 `vocabulary=()` 추가(끝에) + 본문에 `self.vocabulary = list(vocabulary)` 저장.
(b) `_config()` 를 교체:
```python
    def _config(self):
        cfg = {
            "encoding": "wav/pcm",
            "bit_depth": 16,
            "sample_rate": 16000,
            "channels": 1,
            "model": self.model,
            "language_config": {"languages": self.languages, "code_switching": True},
            "messages_config": {
                "receive_partial_transcripts": True,
                "receive_final_transcripts": True,
            },
        }
        if self.vocabulary:
            lang = self.languages[0] if self.languages else "ko"
            cfg["realtime_processing"] = {
                "custom_vocabulary": True,
                "custom_vocabulary_config": {
                    "default_intensity": 0.5,
                    "vocabulary": [
                        {"value": w, "intensity": 0.5, "pronunciations": [], "language": lang}
                        for w in self.vocabulary
                    ],
                },
            }
        return cfg
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_gladia_stt.py -q`
Expected: PASS

- [ ] **Step 5: MeetingSession 이 vocab 전달** — `live_translate.py` `start()`

(a) Gladia 생성에 `vocabulary=self.meta.vocabulary` 추가:
```python
                self._stt = GladiaSTT(
                    config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
                    on_partial=self._stt_partial, on_final=self._stt_final, on_log=self.log,
                    vocabulary=self.meta.vocabulary,
                )
```
(b) RealtimeSTT 분기의 `wb_prompt`/`RealtimeSTTAdapter` 를 교체:
```python
        if self._stt is None:
            wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH) or ""
            vocab_str = ", ".join(self.meta.vocabulary)
            prompt = ", ".join(p for p in (wb_prompt, vocab_str) if p) or None
            self._rt = RealtimeSTTAdapter(
                on_partial=self._stt_partial, on_final=self._stt_final,
                model=self.model, realtime_model=self.realtime_model, language=self.language,
                initial_prompt=prompt, on_log=self.log,
            )
            await self._rt.start()
```

- [ ] **Step 6: 검증 + 커밋**
```bash
.venv/bin/python -c "import live_translate, gladia_stt; print('import ok')"
.venv/bin/python -m pytest -q
git add gladia_stt.py live_translate.py tests/test_gladia_stt.py
git commit -m "feat(stt): 회의 워드북 → Gladia custom_vocabulary / RealtimeSTT initial_prompt"
```
Expected: `import ok`, 전체 통과.

---

## Task 3: controller.start_meeting(meta, interactive) (conversation.py)

**Files:** Modify `conversation.py`, `tests/test_conversation.py`

- [ ] **Step 1: 기존 테스트 갱신 + 신규 테스트** — `tests/test_conversation.py`

(a) 기존 `test_meeting_setup_two_phase_then_input` 의 `await c.start_meeting()` 를
`await c.start_meeting(interactive=True)` 로 변경(대화형일 때만 SETUP 단계).
(b) 파일 끝에 추가:
```python
def test_start_meeting_with_meta_skips_setup():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)
        await c.start_meeting(meta="DIRECT")     # 메타 직접 → 즉시 LIVE
        assert c.mode is Mode.MEETING and c.meeting_phase is MeetingPhase.LIVE
        assert sess.started is True
    asyncio.run(run())


def test_start_meeting_default_no_prompt():
    async def run():
        sess = FakeSession()
        c = make_controller(make_meeting=lambda meta: sess)   # FakeSetup done=True
        await c.start_meeting()                  # interactive=False → 기본값 즉시 시작
        assert c.meeting_phase is MeetingPhase.LIVE
    asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q`
Expected: FAIL (start_meeting 가 meta/interactive 인자 없음)

- [ ] **Step 3: 구현** — `conversation.py` 의 `start_meeting` 전체 교체

```python
    async def start_meeting(self, meta=None, interactive=False):
        if self.mode is Mode.MEETING:
            self.log("회의 모드가 이미 진행 중입니다.")
            return
        await self._teardown()
        self.player.flush()
        self.drain_queue()
        if meta is not None:                       # 웹 폼/직접 메타 → 즉시 시작
            self.mode = Mode.MEETING
            await self._begin_meeting(meta)
            return
        setup = self.make_setup()
        self.mode = Mode.MEETING
        if interactive and not setup.done:         # 콘솔 /meet → 프롬프트
            self.meeting_phase = MeetingPhase.SETUP
            self.meeting_setup = setup
            self._apply_tap()
            self.log("🎤 회의 설정 — 항목을 입력하세요. (Esc 로 취소)")
            self.log(f"   {setup.prompt}")
            return
        await self._begin_meeting(setup.meta)      # 음성/복구 → 기본값 즉시
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_conversation.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(conversation): start_meeting(meta, interactive) — 경로별 메타 공급"
```

---

## Task 4: main 메타 배선 + 웹 회의 폼 (main.py + app.html)

**Files:** Modify `main.py`, `jarvis-web/src/static/app.html`. 검증: pytest + JS 구문 + typecheck.

- [ ] **Step 1: main.py — cmd_ctx /meet 대화형**

`cmd_ctx["start_meeting"] = controller.start_meeting` 를 교체:
```python
    cmd_ctx["start_meeting"] = lambda: controller.start_meeting(interactive=True)
```

- [ ] **Step 2: main.py — meeting_start 메타 파싱**

`_on_remote_command` 의 `elif kind == "meeting_start": await controller.start_meeting()` 를 교체:
```python
            elif kind == "meeting_start":
                from live_translate import MeetingMeta
                title = (msg.get("title") or "").strip() or "회의"
                vocab = [v.strip() for v in (msg.get("vocabulary") or [])
                         if isinstance(v, str) and v.strip()]
                if not vocab:
                    vocab = ["Jarvis", config.USER_NAME]
                await controller.start_meeting(meta=MeetingMeta(
                    my_name=config.USER_NAME, title=title, vocabulary=vocab))
```

- [ ] **Step 3: main.py import + pytest**
```bash
.venv/bin/python -c "import main; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: `import ok`, 전체 통과.

- [ ] **Step 4: app.html — 회의 폼 CSS** (`<style>` 안, `#meeting-loading` 근처)
```css
  #meeting-form { position: fixed; inset: 0; background: #0008; z-index: 26;
    display: flex; align-items: center; justify-content: center; }
  #meeting-form.hidden { display: none; }
  #meeting-form .sheet { background: var(--bg); color: var(--fg); border: 1px solid var(--border);
    border-radius: 14px; padding: 16px; width: min(92vw, 360px); }
  #meeting-form .sheet-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  #meeting-form .sheet-head button { background: transparent; color: var(--fg); padding: 4px 8px; }
  #meeting-form .row { margin: 12px 0; }
  #meeting-form .row > div { font-size: 13px; color: var(--muted); margin-bottom: 6px; }
  #meeting-form input[type="text"] { width: 100%; font-size: 15px; padding: 10px; border-radius: 8px;
    border: 1px solid #888; box-sizing: border-box; }
  #mf-start { width: 100%; margin-top: 12px; }
```

- [ ] **Step 5: app.html — 회의 폼 HTML** (`#meeting-loading` div 다음)
```html
  <div id="meeting-form" class="hidden">
    <div class="sheet">
      <div class="sheet-head"><b>회의 시작</b><button id="mf-cancel">✕</button></div>
      <div class="row"><div>회의 제목</div><input id="mf-title" type="text" placeholder="회의 제목" /></div>
      <div class="row"><div>워드북 (쉼표 구분)</div><input id="mf-vocab" type="text" placeholder="Jarvis, 이름" /></div>
      <button id="mf-start">시작</button>
    </div>
  </div>
```

- [ ] **Step 6: app.html — menu-meet 가 폼 열기 + 폼 핸들러**

기존:
```javascript
  $("menu-meet").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    showMeetingLoading();
    sendControl({ kind: "meeting_start" });
    $("plus-menu").classList.add("hidden");
  });
```
교체:
```javascript
  $("menu-meet").addEventListener("click", () => {
    if (!getPw()) { showLogin(); return; }
    $("plus-menu").classList.add("hidden");
    $("mf-title").value = ""; $("mf-vocab").value = "";
    $("meeting-form").classList.remove("hidden");
    $("mf-title").focus();
  });
  $("mf-cancel").addEventListener("click", () => $("meeting-form").classList.add("hidden"));
  $("meeting-form").addEventListener("click", (e) => { if (e.target === $("meeting-form")) $("meeting-form").classList.add("hidden"); });
  $("mf-start").addEventListener("click", () => {
    const title = $("mf-title").value.trim();
    const vocab = $("mf-vocab").value.split(",").map((s) => s.trim()).filter(Boolean);
    $("meeting-form").classList.add("hidden");
    showMeetingLoading();
    sendControl({ kind: "meeting_start", title, vocabulary: vocab });
  });
```

- [ ] **Step 7: app.html — 서버다운 시 폼 닫기** — `setServerUp` 의 down 분기(`$("settings-modal").classList.add("hidden");` 근처)에 추가:
```javascript
      $("meeting-form").classList.add("hidden");
```

- [ ] **Step 8: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
```
Expected: `JS syntax OK`, typecheck 0.

- [ ] **Step 9: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add main.py jarvis-web/src/static/app.html
git commit -m "feat(meeting): 콘솔 /meet 대화형 + 웹 회의 폼(제목·워드북)"
```

---

## Task 5: 타이틀 웹 헤더 (meeting_title 이벤트)

**Files:** Modify `jarvis-web/src/types.ts`, `jarvis-web/src/meeting_do.ts`, `main.py`, `jarvis-web/src/static/app.html`

- [ ] **Step 1: types.ts** — `EventKind` 에 추가(`| "mic_release"` 근처):
```typescript
  | "meeting_title"        // jarvis → owner: 회의 제목(헤더 표시)
```

- [ ] **Step 2: main.py — _after_meeting_start 에서 발행**

`_after_meeting_start(sess)` 의 `console.log(f"🎤 회의를 시작합니다. ...")` 다음에 추가:
```python
        if web_pub is not None:
            web_pub.emit("meeting_title", sess.meta.title)
```

- [ ] **Step 3: meeting_do.ts — currentView 패턴으로 lastMeetingTitle**

(a) 필드 추가(`private currentView` 근처):
```typescript
  private lastMeetingTitle: string | null = null;
```
(b) `handlePublisherMessage`: `navigate` 케이스에서 home 이면 제목 정리, 그리고 meeting_title 케이스 추가. navigate 케이스를 다음으로:
```typescript
    if (msg.kind === "navigate") {
      this.currentView = msg.text ?? null;
      if (msg.text !== "meeting") this.lastMeetingTitle = null;   // 회의 종료 시 제목 초기화
      this.broadcast(this.buildEvent(msg));
      return;
    }
    if (msg.kind === "meeting_title") {
      this.lastMeetingTitle = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));
      return;
    }
```
(c) `attachViewer`(owner) 의 `currentView === "meeting"` navigate 재전송 다음에:
```typescript
      if (this.lastMeetingTitle) {
        this.safeSend(ws, this.buildEvent({ kind: "meeting_title", text: this.lastMeetingTitle }));
      }
```

- [ ] **Step 4: app.html — handle meeting_title**

`handle()` 의 switch 에 추가(`case "navigate":` 근처):
```javascript
      case "meeting_title":
        meetingTitle = ev.text || "Meeting";
        if (document.body.dataset.view === "meeting") $("title").textContent = meetingTitle;
        return;
```

- [ ] **Step 5: 검증**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main; print('import ok')"
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){console.error('SYNTAX',e.message);bad=1;}}if(bad)process.exit(1);console.log('JS syntax OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, `JS syntax OK`, typecheck 0.

- [ ] **Step 6: 커밋**
```bash
cd /Users/oracle/Documents/concode/jarvis
git add jarvis-web/src/types.ts jarvis-web/src/meeting_do.ts main.py jarvis-web/src/static/app.html
git commit -m "feat(meeting): meeting_title 이벤트로 웹 헤더 제목 동기화"
```

---

## Task 6: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, live_translate, gladia_stt, conversation; print('import ok')"
.venv/bin/python -m pytest -q
node -e "const fs=require('fs');const h=fs.readFileSync('jarvis-web/src/static/app.html','utf8');const m=h.match(/<script>[\s\S]*?<\/script>/g)||[];let bad=0;for(const s of m){const b=s.replace(/^<script>/,'').replace(/<\/script>$/,'');try{new Function(b);}catch(e){bad=1;console.error(e.message);}}if(bad)process.exit(1);console.log('JS OK');"
cd jarvis-web && npm run typecheck
```
Expected: `import ok`, 전체 통과, `JS OK`, typecheck 0.

- [ ] **Step 2: 수동 (배포/재시작 후)**
- 콘솔 `/meet` → "회의 제목?" → "워드북?" 프롬프트 → 시작. 빈 입력은 기본(회의 / Jarvis,이름).
- 웹 +메뉴 미팅 → 폼(제목·워드북) → 시작 → 로딩 → 회의. 헤더에 제목.
- 워드북 단어 발화 시 인식 향상(Gladia custom_vocabulary).
- 음성 "회의 시작"·재시작 복구 → 기본값으로 즉시(프롬프트 없음). 웹 헤더에 "회의".

---

## 비고
- 워드북 빈 입력/미제공 → 기본 `[Jarvis, USER_NAME]`. NER/sentiment 미사용.
- 배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
