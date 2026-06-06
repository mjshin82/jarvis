# 다국어 회의 Phase A (jarvis 코어) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회의를 N개 언어로 — 룸 언어 메타, 단일 멀티타겟 번역(나머지 모든 언어), 언어별 요약, 콘솔 언어 입력. (웹 렌더는 Phase B.)

**Architecture:** 신규 `languages.py` 가 코드 정규화(jp→ja)/매핑. `coach.translate_multi` 가 발화당 1회 LLM 호출로 소스 감지 후 JSON 으로 나머지 언어 번역. `MeetingSession` 이 룸 언어로 STT/번역/트랜스크립트를 구동하고, `_emit` 에 `lang` 을 실어 제네릭 `translation` 이벤트 발행. 요약은 룸 언어마다.

**Tech Stack:** Python 3.11 + pytest. 신규 의존성 없음.

**스펙:** `docs/superpowers/specs/2026-06-06-multilingual-meeting-design.md` (Phase A)

**전제:** 회의 ID/비번/기록(Phase 1·2) 머지됨. `MeetingMeta`/`MeetingSetup`/`_translate_bg`/`record()`/`_save_meeting` 존재. 와이어 포맷이 Phase B(웹)와 묶이므로 **A·B 둘 다 머지 후** jarvis 재시작.

---

## Task 1: languages.py — 코드 정규화/매핑 (신규)

**Files:** Create `languages.py`; Test `tests/test_languages.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_languages.py`

```python
import languages


def test_normalize_alias_and_dedup():
    assert languages.normalize(["jp", "ko", "jp"]) == ["ja", "ko"]


def test_normalize_str_input():
    assert languages.normalize("ko, en, ja") == ["ko", "en", "ja"]


def test_normalize_empty_and_invalid_to_default():
    assert languages.normalize([]) == ["ko", "en"]
    assert languages.normalize("") == ["ko", "en"]
    assert languages.normalize(["xx", "zz"]) == ["ko", "en"]


def test_names_and_gladia():
    assert languages.names(["jp"]) == ["Japanese"]
    assert languages.gladia_codes(["jp", "ko"]) == ["ja", "ko"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_languages.py -q`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: 구현** — `languages.py`

```python
"""회의 언어 코드 정규화/매핑. 입력(웹 jp, 콘솔 'ko,en,ja') → 정규 코드."""

ALIAS = {"jp": "ja"}                       # 사용자 표기 → 표준
NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}
GLADIA = {"ko": "ko", "en": "en", "ja": "ja", "zh": "zh"}
DEFAULT = ["ko", "en"]


def normalize(codes) -> list:
    """리스트 또는 쉼표문자열 → 정규 코드(jp→ja), 유효(NAMES)만, 순서보존 중복제거.
    비거나 전부 무효면 DEFAULT 복사."""
    if isinstance(codes, str):
        codes = codes.split(",")
    out = []
    for c in (codes or []):
        c = (c or "").strip().lower()
        c = ALIAS.get(c, c)
        if c in NAMES and c not in out:
            out.append(c)
    return out or list(DEFAULT)


def names(codes) -> list:
    """정규 코드 → 영어 언어명 리스트 (LLM 프롬프트용)."""
    return [NAMES[c] for c in normalize(codes)]


def gladia_codes(codes) -> list:
    """정규 코드 → Gladia language_config 코드."""
    return [GLADIA[c] for c in normalize(codes)]
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_languages.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**
```bash
git add languages.py tests/test_languages.py
git commit -m "feat(meeting): languages.py — 언어 코드 정규화(jp→ja)/매핑

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: coach.translate_multi + build_multi_system_prompt (coach.py)

**Files:** Modify `coach.py`; Test `tests/test_coach_multi.py`

- [ ] **Step 1: 실패 테스트** — `tests/test_coach_multi.py`

```python
import asyncio
import coach


class _FakeResp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        self._content = content

    class _Chat:
        pass

    @property
    def chat(self):
        outer = self
        comp = type("Comp", (), {
            "create": staticmethod(lambda **kw: _coro(_FakeResp(outer._content)))
        })()
        return type("Chat", (), {"completions": comp})()


async def _coro(v):
    return v


def test_translate_multi_parses_json():
    c = _FakeClient('{"en": "hello", "ja": "こんにちは"}')
    out = asyncio.run(coach.translate_multi(c, "m", "안녕", "sys"))
    assert out == {"en": "hello", "ja": "こんにちは"}


def test_translate_multi_handles_code_fence():
    c = _FakeClient('```json\n{"en": "hi"}\n```')
    out = asyncio.run(coach.translate_multi(c, "m", "안녕", "sys"))
    assert out == {"en": "hi"}


def test_translate_multi_bad_output_returns_empty():
    c = _FakeClient("not json at all")
    out = asyncio.run(coach.translate_multi(c, "m", "안녕", "sys"))
    assert out == {}


def test_translate_multi_empty_text():
    c = _FakeClient('{"en": "x"}')
    out = asyncio.run(coach.translate_multi(c, "m", "   ", "sys"))
    assert out == {}


def test_build_multi_system_prompt_lists_langs():
    p = coach.build_multi_system_prompt(["Korean", "Japanese"], "ctx", ["Concode"])
    assert "Korean" in p and "Japanese" in p and "Concode" in p
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_coach_multi.py -q`
Expected: FAIL (translate_multi/build_multi_system_prompt 없음)

- [ ] **Step 3: 구현** — `coach.py` 끝(또는 translate_meeting 아래)에 추가

```python
_MEET_MULTI_TEMPLATE = """You are a professional simultaneous interpreter for a business meeting.
Room languages: {langs}.
Detect the language of each input utterance and translate it into ALL the OTHER room languages (exclude the source language).
Output ONLY a JSON object mapping language code to translation, e.g. {{"en": "...", "ja": "..."}}. Use codes: ko, en, ja, zh. No commentary, no source-language key, no code fences.

Quality rules:
- Natural and conversational — what a fluent interpreter would actually say, not word-for-word.
- Preserve proper nouns exactly (see glossary); correct STT near-misses.
- Never add information not in the source.

Meeting context:
{context}

Proper nouns glossary (recognize variants, output canonical form):
{glossary}"""


def build_multi_system_prompt(lang_names: list, context: str, glossary_lines: list) -> str:
    """다국어 회의 번역 시스템 프롬프트. 룸 언어 고정 → 회의당 동일(캐시 히트)."""
    glossary = "\n".join(f"- {l}" for l in glossary_lines) if glossary_lines else "- (none)"
    ctx = (context or "").strip() or "(general business meeting)"
    return _MEET_MULTI_TEMPLATE.format(langs=", ".join(lang_names), context=ctx, glossary=glossary)


def _parse_json_obj(s: str) -> dict:
    """LLM 출력에서 JSON 오브젝트만 추출(코드펜스/잡텍스트 방어). 실패 시 {}."""
    import json
    import re
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "{" in s:
            s = s[s.find("{"):]
    try:
        d = json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return {}
        try:
            d = json.loads(m.group(0))
        except Exception:
            return {}
    if not isinstance(d, dict):
        return {}
    return {str(k): str(v) for k, v in d.items() if isinstance(v, str) and v.strip()}


async def translate_multi(client, model: str, text: str,
                          system_prompt: str, extra: dict | None = None) -> dict:
    """발화 1건을 룸의 나머지 언어들로 번역(단일 호출, JSON). 실패/빈 입력 → {}."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=800,
            temperature=0.2,
            extra_body=extra or {},
        )
        out = (r.choices[0].message.content or "").strip()
        return _parse_json_obj(out)
    except Exception as ex:
        print(f"[coach] translate_multi fallback: {ex}")
        return {}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_coach_multi.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**
```bash
git add coach.py tests/test_coach_multi.py
git commit -m "feat(meeting): coach.translate_multi — 단일 호출 멀티타겟 번역(JSON)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: relay_client emit lang 인자 (relay_client.py)

**Files:** Modify `relay_client.py`; Test `tests/test_relay_client.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_relay_client.py` 끝에

```python
def test_emit_includes_lang_when_set():
    rc = _rc()
    rc.emit("translation", "hello", lang="ja")
    assert rc._queue.get_nowait() == {"kind": "translation", "text": "hello", "lang": "ja"}


def test_emit_omits_lang_when_empty():
    rc = _rc()
    rc.emit("source", "안녕")
    assert rc._queue.get_nowait() == {"kind": "source", "text": "안녕"}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q`
Expected: FAIL (lang 인자 없음)

- [ ] **Step 3: 구현** — `relay_client.py`

`emit` 과 `emit_async` 를 교체:
```python
    def emit(self, kind: str, text: str = "", lang: str = "") -> None:
        """이벤트 enqueue (동기). 큐가 가득 차면 드롭(콘솔에 경고)."""
        msg = {"kind": kind, "text": text}
        if lang:
            msg["lang"] = lang
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.on_log("[relay] 큐 가득참 — 메시지 드롭")

    async def emit_async(self, kind: str, text: str = "", lang: str = "") -> None:
        """MeetingSession.add_listener 가 async 콜백을 기대하므로 await 가능 래퍼."""
        self.emit(kind, text, lang)
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_relay_client.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과(기존 test_emit_enqueues_json_dict 도 lang 미포함이라 통과).

- [ ] **Step 5: 커밋**
```bash
git add relay_client.py tests/test_relay_client.py
git commit -m "feat(relay): emit/emit_async 에 lang 인자(번역 언어) 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: MeetingMeta.languages + 트랜스크립트 shape + STT 언어 (live_translate.py)

**Files:** Modify `live_translate.py`, `tests/test_meeting_session.py`

- [ ] **Step 1: 기존 테스트 갱신** — `tests/test_meeting_session.py`

`test_record_line_and_translation` 와 `test_record_shape` 를 새 shape 로 교체:
```python
def test_record_line_and_translation():
    sess = _sess()
    entry = sess._record_line("hello")
    assert entry["source"] == "hello" and entry["translations"] == {}
    assert sess._transcript == [entry]
    entry["translations"]["ko"] = "안녕"
    assert sess._transcript[0]["translations"]["ko"] == "안녕"


def test_record_shape():
    from live_translate import MeetingMeta, hash_password
    sess = _sess(meta=MeetingMeta(title="주간", password="pw", languages=["ko", "en"]))
    sess.meta.meeting_id = "abc123"
    sess.meta.started_at = "2026-06-06T10:00:00"
    sess._record_line("hi")
    rec = sess.record()
    assert rec["id"] == "abc123"
    assert rec["title"] == "주간"
    assert rec["languages"] == ["ko", "en"]
    assert rec["transcript"][0]["source"] == "hi"
    assert rec["transcript"][0]["translations"] == {}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q`
Expected: FAIL (entry 에 ko/en, record 에 languages 없음)

- [ ] **Step 3: 구현** — `live_translate.py`

(a) import 블록에 `import languages` 추가(예: `import settings` 줄 아래).
(b) `MeetingMeta` 에 필드 추가(`vocabulary` 줄 아래):
```python
    languages: list = field(default_factory=lambda: ["ko", "en"])   # 룸 언어(정규 코드)
```
(c) `_record_line` 를 교체:
```python
    def _record_line(self, source: str) -> dict:
        """확정 원문 1줄을 트랜스크립트에 추가하고 entry 반환(번역은 나중에 채움)."""
        entry = {"ts": now_iso(), "source": source, "src_lang": "", "translations": {}}
        self._transcript.append(entry)
        return entry
```
(d) `record()` 의 반환 dict 에 `"languages"` 추가(`"transcript"` 줄 위 또는 아래):
```python
            "languages": list(self.meta.languages),
```
(e) `start()` 의 Gladia 언어 선택 교체 — `langs = [s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()]` 를:
```python
                langs = languages.gladia_codes(self.meta.languages)
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "feat(meeting): MeetingMeta.languages + 트랜스크립트 translations 맵 + STT 룸 언어

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 멀티타겟 번역 흐름 — _emit(lang) + _setup_translator + _translate_bg (live_translate.py)

**Files:** Modify `live_translate.py`, `tests/test_meeting_session.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/test_meeting_session.py` 끝에

```python
def test_translate_bg_emits_per_language(monkeypatch):
    import types as _types
    import coach

    async def fake_multi(client, model, text, system_prompt, extra=None):
        return {"en": "hi", "ja": "ya"}

    monkeypatch.setattr(coach, "translate_multi", fake_multi)
    sess = _sess()
    sess.llm = _types.SimpleNamespace(client=None, extra={})
    sess._tx_client = object()
    sess._tx_model = "m"
    sess._tx_system = "sys"
    got = []
    sess.add_listener(lambda kind, text, lang="": got.append((kind, text, lang)))
    entry = {"ts": "t", "source": "안녕", "src_lang": "", "translations": {}}
    asyncio.run(sess._translate_bg("안녕", entry))
    assert entry["translations"] == {"en": "hi", "ja": "ya"}
    assert ("translation", "hi", "en") in got
    assert ("translation", "ya", "ja") in got
```
(`import asyncio` 는 파일 상단에 이미 있음.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py::test_translate_bg_emits_per_language -q`
Expected: FAIL (현 _translate_bg 는 단일 translation_ko/en, _emit 에 lang 없음)

- [ ] **Step 3: 구현** — `live_translate.py`

(a) `_emit` 를 교체(lang 인자 + translation 국기 + listener 3-arg):
```python
    def _emit(self, kind: str, text: str, lang: str = "") -> None:
        """회의 이벤트를 콘솔 + listener 들로 fan-out. translation 은 lang 동반."""
        flags = {"ko": "🇰🇷", "en": "🇺🇸", "ja": "🇯🇵", "zh": "🇨🇳"}
        prefix = {"source": "🧑", "info": "🎤", "gap": ""}.get(kind, "")
        if kind == "translation":
            prefix = flags.get(lang, "🌐")
        if kind == "gap":
            self.log("")
        elif kind == "partial":
            pass
        elif prefix:
            self.log(f"{prefix} {text}")
        else:
            self.log(text)
        for cb in self._listeners:
            try:
                result = cb(kind, text, lang)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                self.log(f"[meet] listener error: {e}")
```
(b) `_setup_translator` 의 시스템 프롬프트 빌드 부분 교체 — `glossary = ...` 부터 `self._tx_system = coach._build_meet_system_prompt(...)` 까지를:
```python
        glossary = wordbook.load_glossary_lines(path=wordbook.MEET_PATH)
        ctx = config.MEET_CONTEXT.strip()
        self._tx_system = coach.build_multi_system_prompt(
            languages.names(self.meta.languages), ctx, glossary)
```
(use_remote 이하 클라이언트 선택 블록은 변경 없음.)
(c) `_translate_bg` 전체 교체:
```python
    async def _translate_bg(self, text: str, entry: dict | None = None):
        """룸의 나머지 모든 언어로 번역(단일 호출). 각 언어를 translation 이벤트로 emit."""
        extra = self.llm.extra if self._tx_client is self.llm.client else {}
        try:
            out = await coach.translate_multi(
                self._tx_client, self._tx_model, text, self._tx_system, extra=extra,
            )
        except Exception as ex:
            self.log(f"[meet] translate error: {ex}")
            return
        for lang, t in out.items():
            if entry is not None:
                entry["translations"][lang] = t
            self._emit("translation", t, lang)
```

- [ ] **Step 4: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py -q && .venv/bin/python -m pytest -q`
Expected: 모두 통과.

- [ ] **Step 5: 커밋**
```bash
git add live_translate.py tests/test_meeting_session.py
git commit -m "feat(meeting): 멀티타겟 번역 — translate_multi + 제네릭 translation(lang) emit

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 콘솔 언어 단계 + 웹 meeting_start languages (live_translate.py + main.py)

**Files:** Modify `live_translate.py`, `main.py`, `tests/test_meeting_session.py`, `tests/test_conversation.py`

- [ ] **Step 1: 테스트 갱신/추가**

(a) `tests/test_meeting_session.py` — 기존 `test_setup_submit_title_and_vocab` 와 `test_setup_password_empty_stays_blank` 를 **삭제**하고 다음 두 개로 교체:
```python
def test_setup_all_steps():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit("주간회의")            # title
    s.submit("ko, en, jp")          # languages (jp→ja)
    s.submit("신명진, 콘코드")      # vocabulary
    assert not s.done               # password 남음
    s.submit("secret")              # password
    assert s.done
    assert s.meta.title == "주간회의"
    assert s.meta.languages == ["ko", "en", "ja"]
    assert s.meta.vocabulary == ["신명진", "콘코드"]
    assert s.meta.password == "secret"


def test_setup_empty_uses_defaults():
    from live_translate import MeetingSetup
    s = MeetingSetup(default_my_name="민준")
    s.submit(""); s.submit(""); s.submit(""); s.submit("")
    assert s.done
    assert s.meta.languages == ["ko", "en"]
    assert s.meta.password == ""
```
(b) `tests/test_conversation.py` — `test_meeting_setup_empty_accepts_defaults` 의 빈 입력 단계를 4단계로 갱신:
```python
        await c.on_text("")                      # title 단계 Enter=기본
        assert c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("")                      # languages 단계 Enter=기본
        assert c.meeting_phase is MeetingPhase.SETUP
        await c.on_text("")                      # vocabulary 단계 Enter=기본
        assert c.meeting_phase is MeetingPhase.SETUP   # 아직 password 단계
        await c.on_text("")                      # password 단계 → 시작
        assert c.meeting_phase is MeetingPhase.LIVE
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py tests/test_conversation.py -q`
Expected: FAIL (languages 단계 없음)

- [ ] **Step 3: live_translate.py 구현**

(a) `_META_STEPS` 를 교체(languages 를 title 다음 2번째로):
```python
_META_STEPS = (
    ("title", "회의 제목을 입력하세요 (Enter=기본)"),
    ("languages", "언어 코드 — 쉼표로 (Enter=기본: ko,en)"),
    ("vocabulary", "워드북 — 쉼표로 구분 (Enter=기본: Jarvis, 이름)"),
    ("password", "비번 (Enter=자동 생성)"),
)
```
(b) `MeetingSetup.submit` 에 languages 분기 추가(`if key == "title":` 다음, `elif key == "vocabulary":` 앞):
```python
        elif key == "languages":
            self.meta.languages = languages.normalize(v)   # 빈 입력 → DEFAULT(ko,en)
```

- [ ] **Step 4: main.py 구현**

(a) 상단 import 에 `import languages` 추가(예: `import settings` 근처).
(b) `_on_remote_command` 의 `elif kind == "meeting_start":` 블록에서 `password = ...` 다음에 추가하고 `MeetingMeta(...)` 에 languages 전달:
```python
                password = (msg.get("password") or "").strip()
                langs = languages.normalize(msg.get("languages") or [])
                await controller.start_meeting(meta=MeetingMeta(
                    my_name=config.USER_NAME, title=title, vocabulary=vocab,
                    password=password, languages=langs))
```

- [ ] **Step 5: 통과 + 전체**

Run: `.venv/bin/python -m pytest tests/test_meeting_session.py tests/test_conversation.py -q && .venv/bin/python -c "import main; print('import ok')" && .venv/bin/python -m pytest -q`
Expected: 모두 통과, `import ok`.

- [ ] **Step 6: 커밋**
```bash
git add live_translate.py main.py tests/test_meeting_session.py tests/test_conversation.py
git commit -m "feat(meeting): 콘솔 /meet 언어 단계 + 웹 meeting_start languages

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 언어별 요약 — llm.summarize(lang_name) + main._save_meeting (llm.py + main.py)

**Files:** Modify `llm.py`, `main.py`, `tests/test_llm_summarize.py`

- [ ] **Step 1: 테스트 갱신** — `tests/test_llm_summarize.py` 의 `test_summarize_calls_client` 를 교체(lang_name 인자 + 프롬프트 확인):
```python
def test_summarize_calls_client():
    llm = LLM()
    llm._mock = False
    captured = {}

    class FakeMsg:
        content = "요약본"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kw):
            captured.update(kw)
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    llm.client = FakeClient()
    llm.model = "m"
    llm.extra = {}
    out = asyncio.run(llm.summarize("회의 원문", "Japanese"))
    assert out == "요약본"
    assert "Japanese" in captured["messages"][0]["content"]
    assert "회의 원문" in captured["messages"][-1]["content"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_llm_summarize.py -q`
Expected: FAIL (summarize 가 lang_name 인자 없음 / 프롬프트 고정 한국어)

- [ ] **Step 3: llm.py 구현** — `summarize` 교체:
```python
    async def summarize(self, text: str, lang_name: str = "Korean") -> str:
        """회의 트랜스크립트 1회 요약(대상 언어). 현재 백엔드 사용. mock/미설정 → ""."""
        if self._mock or self.client is None or not (text or "").strip():
            return ""
        messages = [
            {"role": "system", "content":
             f"Summarize the meeting conversation concisely in {lang_name}. "
             f"Use bullet points covering key discussion, decisions, and action items."},
            {"role": "user", "content": text},
        ]
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, extra_body=self.extra,
        )
        return (resp.choices[0].message.content or "").strip()
```

- [ ] **Step 4: main.py 구현** — `_save_meeting` 의 `_run` 내부 요약 부분 교체

상단 import 에 `import languages` 가 Task 6 에서 추가됨(없으면 추가). `_run` 의 `text = "\n".join(...)` 부터 끝까지를 교체:
```python
            lines = record.get("transcript") or []
            if not lines:
                return
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
            if summaries:
                try:
                    await asyncio.to_thread(
                        store.set_summary, record["id"],
                        json.dumps(summaries, ensure_ascii=False))
                    console.log(f"📝 회의 요약 저장됨 (ID {record['id']}, {','.join(summaries)})")
                except Exception as e:
                    console.log(f"요약 저장 실패: {e}")
```

- [ ] **Step 5: 통과 + import + 전체**

Run:
```bash
.venv/bin/python -m pytest tests/test_llm_summarize.py -q
.venv/bin/python -c "import main, languages; print('import ok')"
.venv/bin/python -m pytest -q
```
Expected: PASS, `import ok`, 전체 통과.

- [ ] **Step 6: 커밋**
```bash
git add llm.py main.py tests/test_llm_summarize.py
git commit -m "feat(meeting): 언어별 요약 — summarize(lang_name) + 룸 언어마다 저장({lang:요약})

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 최종 검증

- [ ] **Step 1: 전체**
```bash
cd /Users/oracle/Documents/concode/jarvis
.venv/bin/python -c "import main, live_translate, coach, llm, languages; print('import ok')"
.venv/bin/python -m pytest -q
grep -rn "translation_ko\|translation_en\|is_korean" live_translate.py || echo "meeting no longer uses ko/en kinds"
```
Expected: `import ok`, 전체 통과. live_translate 에 translation_ko/en·is_korean 잔존 없음(번역은 translate_multi 로).

- [ ] **Step 2: 수동 (Phase B 머지·재시작 후 — A 단독으론 콘솔만)**
- `/meet` → 제목 → 언어(예 `ko,en,ja`) → 워드북 → 비번 단계.
- ko 발화 → 콘솔에 🇺🇸 영어 + 🇯🇵 일본어 동시. ja 발화 → 🇰🇷 + 🇺🇸.
- `/stop` → `meetings.db` summary 가 `{"ko":..,"en":..,"ja":..}` JSON.
- `sqlite3 meetings.db "select languages... "` 대신 `select summary from meetings;` 로 언어별 요약 확인.

---

## 비고
- Phase B(웹: types/PUBLIC_KINDS, viewer/app 제네릭 translation 렌더, 폼 언어 체크박스, badge)는 별도 플랜. **A·B 둘 다 머지 후** jarvis 재시작(옛 웹↔새 jarvis 깨짐 방지).
- `coach.translate_meeting`/`is_korean` 는 제거하지 않음(타 경로 대비). 회의만 translate_multi 사용.
- 배포: 웹 변경 없음(A 단독). origin push 직접.
