# 회의 메타(타이틀 + 워드북) 입력 설계

날짜: 2026-06-06

## 목표
회의 시작 시 **타이틀**과 **워드북(쉼표 구분 단어)**을 입력받아, 워드북을 STT 인식 보강에 사용한다.
- 콘솔 `/meet`: 대화형 프롬프트(제목 → 워드북).
- 웹 `+메뉴 미팅`: 입력 폼(제목 + 워드북).
- 음성("회의 시작")·부팅 복구: 기본값으로 즉시 시작(프롬프트 없음).
- 워드북 → Gladia `custom_vocabulary`(+ 로컬 RealtimeSTT `initial_prompt`).
- 타이틀 → 웹 헤더 표시.

기본 워드북 = `["Jarvis", config.USER_NAME]`.

## 비범위 (YAGNI)
- Gladia NER/sentiment/translation 플래그(예시에 있으나 미사용).
- 워드북을 번역기(coach glossary)에 주입(STT 전용).
- 워드북 영속(매 회의 입력; 파일 wordbook_meet.txt 와 별개).

---

## A. 메타 모델 + 콘솔 대화형 (live_translate.py)

### MeetingMeta
`@dataclass` 에 필드 추가:
```python
    title: str = ""
    vocabulary: list = field(default_factory=list)   # STT 보강 단어
```
(`from dataclasses import dataclass, field` — field 사용. key 프로퍼티 등 기존 유지.)

### MeetingSetup (2단계 부활)
```python
_META_STEPS = (
    ("title", "회의 제목을 입력하세요 (Enter=기본)"),
    ("vocabulary", "워드북 — 쉼표로 구분 (Enter=기본: Jarvis, <이름>)"),
)
```
- `__init__(default_my_name)`: `self._default_vocab = ["Jarvis", default_my_name]`,
  `self.meta = MeetingMeta(my_name=default_my_name, title="회의", vocabulary=list(self._default_vocab))`.
- `submit(value)`:
  - step key `"title"`: `self.meta.title = value.strip() or "회의"`.
  - step key `"vocabulary"`: 빈 입력이면 기본 유지, 아니면 `[w.strip() for w in value.split(",") if w.strip()]`.
  - `step_index += 1`.
  (현 generic setattr 대신 두 키 명시 처리.)

---

## B. 워드북 → STT (gladia_stt.py + live_translate.py)

### GladiaSTT
- `__init__(..., vocabulary=())` 추가 → `self.vocabulary = list(vocabulary)`.
- `_config()`: vocab 있을 때만 `realtime_processing` 추가(없으면 기존 그대로):
  ```python
  cfg = { ... 기존 ... }
  if self.vocabulary:
      cfg["realtime_processing"] = {
          "custom_vocabulary": True,
          "custom_vocabulary_config": {
              "default_intensity": 0.5,
              "vocabulary": [
                  {"value": w, "intensity": 0.5, "pronunciations": [], "language": self.languages[0]}
                  for w in self.vocabulary
              ],
          },
      }
  return cfg
  ```

### MeetingSession (live_translate.py start())
- Gladia 분기: `GladiaSTT(..., vocabulary=self.meta.vocabulary)`.
- 로컬(RealtimeSTT) 분기: initial_prompt 에 vocab 합침:
  ```python
  wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH) or ""
  vocab_str = ", ".join(self.meta.vocabulary)
  prompt = ", ".join(p for p in (wb_prompt, vocab_str) if p) or None
  self._rt = RealtimeSTTAdapter(..., initial_prompt=prompt, ...)
  ```
- 회의 시작 로그에 제목 포함: `🎤 회의 시작: {meta.title}`.

(일반 대화 Gladia(ConversationSTT make_gladia)는 vocabulary 미전달 → 기본 () → 영향 없음.)

---

## C. 진입 경로별 메타 공급 (conversation.py + main.py + web)

### controller.start_meeting
```python
async def start_meeting(self, meta=None, interactive=False):
    if self.mode is Mode.MEETING:
        self.log("회의 모드가 이미 진행 중입니다."); return
    await self._teardown(); self.player.flush(); self.drain_queue()
    if meta is not None:                      # 웹 폼 / 직접 메타
        self.mode = Mode.MEETING
        await self._begin_meeting(meta); return
    setup = self.make_setup()                 # 기본 메타(+steps) 보유
    self.mode = Mode.MEETING
    if interactive and not setup.done:        # 콘솔 /meet → 프롬프트
        self.meeting_phase = MeetingPhase.SETUP
        self.meeting_setup = setup
        self._apply_tap()
        self.log("🎤 회의 설정 — 항목을 입력하세요. (Esc 로 취소)")
        self.log(f"   {setup.prompt}")
        return
    await self._begin_meeting(setup.meta)     # 음성/복구 → 기본값 즉시
```
(기존 SETUP/`_handle_setup_input` 흐름·`_begin_meeting` 유지. persist_mode("meeting") 은 `_begin_meeting` 에서.)

### main.py
- cmd_ctx: `cmd_ctx["start_meeting"] = lambda: controller.start_meeting(interactive=True)` (콘솔 /meet 대화형).
- `_on_remote_command` `meeting_start`:
  ```python
  elif kind == "meeting_start":
      from live_translate import MeetingMeta
      title = (msg.get("title") or "").strip() or "회의"
      vocab = [v.strip() for v in (msg.get("vocabulary") or []) if isinstance(v, str) and v.strip()]
      if not vocab:
          vocab = ["Jarvis", config.USER_NAME]
      await controller.start_meeting(meta=MeetingMeta(my_name=config.USER_NAME, title=title, vocabulary=vocab))
  ```
- 음성 intent("meeting")·부팅 복구: 기존대로 `controller.start_meeting()` (meta=None, interactive=False → 기본값).

### 웹 폼 (app.html)
- 신규 모달 `#meeting-form`(settings-modal 스타일 재사용): 제목 input(placeholder "회의 제목") + 워드북 input(placeholder "Jarvis, 이름 (쉼표 구분)") + [시작]/[취소].
- `menu-meet` 클릭 → 폼 열기(현 즉시 meeting_start 대신). [시작] →
  ```javascript
  const title = $("mf-title").value.trim();
  const vocab = $("mf-vocab").value.split(",").map(s=>s.trim()).filter(Boolean);
  sendControl({ kind: "meeting_start", title, vocabulary: vocab });
  showMeetingLoading();
  $("meeting-form").classList.add("hidden");
  ```
  [취소] → 폼 닫기. (서버다운 시 폼도 닫힘 — setServerUp.)

---

## D. 타이틀 웹 헤더 표시 (types.ts + meeting_do.ts + app.html + main.py)

신규 owner 이벤트 `meeting_title`(text=제목), currentView 패턴과 동일:
- `types.ts`: `EventKind` 에 `"meeting_title"` 추가(PUBLIC_KINDS 미포함).
- `main.py` `_after_meeting_start(sess)`: `if web_pub: web_pub.emit("meeting_title", sess.meta.title)` 추가(기존 navigate/URL 로그 옆).
- `meeting_do.ts`:
  - 필드 `private lastMeetingTitle: string | null = null;`
  - `handlePublisherMessage`: `meeting_title` → `this.lastMeetingTitle = msg.text ?? null; this.broadcast(...); return;` (append 없음). `navigate` 케이스에서 home 이면 `this.lastMeetingTitle = null` (회의 종료 정리).
  - `attachViewer`(owner): `if (this.lastMeetingTitle) safeSend(navigate 다음) meeting_title`.
- `app.html` handle():
  ```javascript
  case "meeting_title":
    meetingTitle = ev.text || "Meeting";
    if (document.body.dataset.view === "meeting") $("title").textContent = meetingTitle;
    return;
  ```
  → 콘솔/음성/복구로 시작한 회의도 웹 헤더에 제목 반영. 웹 폼으로 시작 시에도 jarvis 가 동일 이벤트로 회신.

---

## 데이터 흐름
입력(콘솔 프롬프트 / 웹 폼 / 기본) → `MeetingMeta(title, vocabulary)` → `controller.start_meeting` → `_begin_meeting`
→ `MeetingSession(meta)` → Gladia `custom_vocabulary` / RealtimeSTT `initial_prompt`.
제목 → `meeting_title` 이벤트 → 웹 헤더(DO 가 재접속 동기화·종료 시 초기화).

## 테스트
- `tests/test_meeting_session.py` 또는 신규: `MeetingSetup` submit — title/vocabulary 파싱·기본값(빈 입력→[Jarvis,<name>], 쉼표 분리). `MeetingMeta` 필드.
- `tests/test_gladia_stt.py`: `GladiaSTT(vocabulary=["신명진"])._config()` 에 `realtime_processing.custom_vocabulary_config.vocabulary[0]["value"]=="신명진"`; vocab 없으면 `realtime_processing` 키 부재.
- 웹/DO(폼·meeting_title): `npm run typecheck` + JS 구문 + 수동.

## 검증
- `.venv/bin/python -m pytest -q` 통과 + import.
- `cd jarvis-web && npm run typecheck` 0, app.html JS 구문 OK.
- 수동(배포/재시작 후): 콘솔 /meet → 제목·워드북 프롬프트 → 시작; 웹 +메뉴 미팅 → 폼 → 시작; 발화 시 워드북 단어 인식 향상; 웹 헤더에 제목; 음성/복구 회의는 기본값.

## 영향 파일
| 파일 | 변경 |
|------|------|
| `live_translate.py` | MeetingMeta(title/vocabulary), MeetingSetup 2단계, MeetingSession vocab→STT |
| `gladia_stt.py` | vocabulary → custom_vocabulary |
| `conversation.py` | `start_meeting(meta, interactive)` |
| `main.py` | cmd_ctx /meet interactive, meeting_start 메타 파싱, after_meeting_start meeting_title |
| `jarvis-web/src/types.ts` | `meeting_title` kind |
| `jarvis-web/src/meeting_do.ts` | lastMeetingTitle 추적·재접속·종료 정리 |
| `jarvis-web/src/static/app.html` | 회의 폼 모달 + meeting_title handle |
| `tests/test_*` | MeetingSetup/Gladia vocab 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
