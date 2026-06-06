# 다국어 회의 설계

날짜: 2026-06-06

## 목표
회의를 ko↔en 양방향 고정에서 **N개 언어** 회의로 확장한다.
1. 메타에 룸 언어 집합 입력 — 웹 4-체크박스(ko/en/jp/zh), 콘솔 쉼표 코드.
2. 번역 — 각 발화의 소스 언어를 감지해 **룸의 나머지 모든 언어**로 번역(발화당 단일 LLM 호출).
3. 요약 — 룸 언어마다 각각 요약.

## 핵심 결정
- 언어 코드 정규화: 입력은 `jp` 허용하되 **내부는 표준 `ja`**(Gladia/LLM 기대값). `MeetingMeta.languages` 는 정규 코드 리스트.
- 번역: **단일 멀티타겟 호출** — LLM 이 소스 감지 후 나머지 언어로 번역, JSON 반환. 시스템 프롬프트 회의당 고정(DeepSeek 캐시).
- 와이어 포맷: 고정 `translation_ko/en` → **제네릭 `translation` + `lang`**. 트랜스크립트 entry `{ts, source, src_lang, translations:{lang:text}}`.

## 비범위 (YAGNI)
- 웹에서 4개 외 언어 선택(콘솔은 임의 코드 가능). 언어별 글로서리. 번역 품질 튜닝(few-shot 언어별).
- RealtimeSTT 폴백의 다국어 최적화(언어 인자 기존 유지). `coach.translate_meeting`(구 양방향) 은 제거하지 않고 둠(타 호출 대비) — 회의만 `translate_multi` 사용.
- 요약을 위한 스키마 변경(기존 `summary TEXT` 에 JSON 저장).

---

## languages.py (신규)

언어 코드 ↔ 이름/Gladia코드 매핑 + 정규화. 단일 책임, 테스트 용이.

```python
ALIAS = {"jp": "ja"}                       # 사용자 표기 → 표준
NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}
GLADIA = {"ko": "ko", "en": "en", "ja": "ja", "zh": "zh"}
DEFAULT = ["ko", "en"]

def normalize(codes) -> list[str]:
    """입력 코드 리스트/문자열 → 정규 코드(jp→ja), 유효(NAMES)만, 순서 보존 중복제거. 비면 DEFAULT."""

def names(codes) -> list[str]:             # ["Korean", "Japanese"]
def gladia_codes(codes) -> list[str]:      # Gladia language_config 용
```

(`normalize` 는 `str`(쉼표분리) 와 `list` 둘 다 수용.)

---

## Phase A — jarvis 코어 (순수 Python)

### MeetingMeta (live_translate.py)
- 필드 추가: `languages: list = field(default_factory=lambda: ["ko", "en"])`.
- `partner_lang`/`my_lang` 는 더 이상 번역에 안 쓰임 — 필드는 남기되(웹 hello 호환) `_setup_translator` 의 컨텍스트 주입은 룸 언어 기반으로 교체.

### 번역 (coach.py + live_translate.py)
- `coach.translate_multi(client, model, text, system_prompt, extra=None) -> dict`:
  - 단일 호출. system_prompt 는 호출자가 룸 언어로 빌드. 응답을 `json.loads` 로 파싱해 `{lang: text}` 반환. 파싱 실패/예외 → `{}`(로그). 빈 입력 → `{}`.
- `coach.build_multi_system_prompt(target_langs_names: list[str], context: str, glossary_lines) -> str`:
  - "회의 번역기. 룸 언어: {names}. 입력 발화의 언어를 감지하고 **소스를 제외한 나머지 룸 언어들**로 번역하라. 출력은 JSON 오브젝트만: {\"en\":\"..\",\"ja\":\"..\"} (소스 언어 키 생략). 코드: ko/en/ja/zh." + 글로서리/컨텍스트.
- `MeetingSession._setup_translator()`: 시스템 프롬프트를 `build_multi_system_prompt(languages.names(self.meta.languages), ctx, glossary)` 로 빌드. (이름 컨텍스트는 룸 언어 중심으로.)
- `MeetingSession._translate_bg(text, entry)`: `is_korean` 분기 제거 →
  ```
  out = await coach.translate_multi(client, model, text, self._tx_system, extra)
  for lang, t in out.items():
      if entry is not None: entry["translations"][lang] = t
      self._emit("translation", t, lang=lang)
  ```
- `_emit(kind, text, lang="")`: 시그니처에 `lang` 추가. 콘솔 prefix 는 lang 별 국기(ko🇰🇷 en🇺🇸 ja🇯🇵 zh🇨🇳, 기본 🌐). listener 콜백에 `(kind, text, lang)` 전달 — 단, 기존 listener(web_pub.emit_async) 는 `(kind, text)` 시그니처라 **호환 처리**: `_emit` 이 listener 호출 시 translation 이면 lang 을 어떻게 넘길지 → web_pub.emit 은 `(kind, text)` 만 받으므로, lang 을 text 와 분리해 보낼 방법 필요.

  **결정:** listener 인터페이스를 `(kind, text, lang="")` 로 확장. `RelayClient` 의 **`emit(kind, text="", lang="")` 와 리스너로 쓰이는 `emit_async(kind, text="", lang="")` 둘 다** lang 인자를 받고 메시지 dict 에 `lang` 포함(빈 값이면 생략 가능). 기존 호출부(emit("navigate","home"), emit("meeting_creds", json), `_emit("source", text)` 등)는 lang 기본값으로 무영향.

### STT 언어 (live_translate.py start())
- Gladia: `languages = languages.gladia_codes(self.meta.languages)` (정적 config.MEET_GLADIA_LANGUAGES 대신; 메타 비면 normalize 가 DEFAULT 보장). RealtimeSTT 폴백 변경 없음.

### 트랜스크립트 (live_translate.py)
- `_record_line(source)`: `{"ts": now_iso(), "source": source, "src_lang": "", "translations": {}}`.
- `record()`: 기존 + `"languages": list(self.meta.languages)` 추가.

### 요약 (llm.py + main.py)
- `llm.summarize(text, lang_name="Korean")`: 시스템 프롬프트 "Summarize the meeting conversation concisely in {lang_name}. 불릿, 주요 논의·결정·할 일." mock/빈 → "".
- `main._save_meeting`: 트랜스크립트 텍스트(원문+번역) 빌드 후, `record["languages"]` 마다 `summarize(text, NAMES[lang])` 호출 → `{lang: summary}` dict → `json.dumps` → `store.set_summary(id, json)`. (백그라운드 1태스크 내 순차 N회.)

### 입력 경로 (live_translate.py + main.py)
- `MeetingSetup`: `_META_STEPS` 에 `("languages", "언어 코드 — 쉼표로 (Enter=기본: ko,en)")` 추가(password 다음 또는 앞 — vocabulary 다음, password 앞). `submit` 에 `elif key == "languages": self.meta.languages = languages.normalize(v) if v else ["ko","en"]`.
- `main._on_remote_command` meeting_start: `langs = languages.normalize(msg.get("languages") or [])` → `MeetingMeta(..., languages=langs)`.

---

## Phase B — 웹 (jarvis-web)

### types.ts
- `EventKind`: `translation_ko`/`translation_en` 제거, `translation` 추가.
- `RelayEvent`/`ClientMessage` 에 `lang?: string` 추가(이미 text 기반; lang 필드 신설).
- `MeetingMeta`(웹): `languages?: string[]` 추가.

### meeting_do.ts
- `PUBLIC_KINDS`: `translation_ko`/`translation_en` → `translation` 으로 교체(자막 공개 유지).

### viewer.html / app.html
- `case "translation_ko"`/`"translation_en"` 통합 → `case "translation":`
  ```
  const map = {ko:["🇰🇷","ko"], en:["🇺🇸","en"], ja:["🇯🇵","ja"], zh:["🇨🇳","zh"]};
  const [sym, cls] = map[ev.lang] || ["🌐", "tx"];
  // <div class="tx LANG">{sym} {text}</div>
  ```
- CSS: `--ja`/`--zh` 색 추가, `.tx.ja`/`.tx.zh`. (기존 --ko/--en 유지.)
- `applyMeta`: 언어 badge 를 `meta.languages` 기반으로(예: "ko · en · ja"). partner_lang/user_lang 표시는 제거 또는 languages 로 대체.

### app.html 회의 폼
- `#meeting-form` 에 언어 행 추가: 체크박스 4개
  ```html
  <div class="row"><div>언어</div>
    <label><input type="checkbox" class="mf-lang" value="ko" checked> 한국어</label>
    <label><input type="checkbox" class="mf-lang" value="en" checked> English</label>
    <label><input type="checkbox" class="mf-lang" value="jp"> 日本語</label>
    <label><input type="checkbox" class="mf-lang" value="zh"> 中文</label>
  </div>
  ```
- `mf-start`: `const languages = [...document.querySelectorAll(".mf-lang:checked")].map(c=>c.value);` → `sendControl({kind:"meeting_start", ..., languages})`. (jp 는 jarvis 가 ja 로 정규화.)
- `menu-meet` 폼 리셋 시 체크박스 기본(ko·en) 복원.

---

## 데이터 흐름
입력(웹 체크박스 jp·.. / 콘솔 "ko,en,ja") → `languages.normalize` → `MeetingMeta.languages`(정규) → STT(gladia_codes) + 번역 시스템 프롬프트(names). 발화 → STT final → `translate_multi`(소스 감지, 나머지 언어 JSON) → 각 lang `translation` 이벤트 + entry.translations. 종료 → record(languages 포함) → 언어별 요약 `{lang:summary}` → DB. 웹: `translation` 이벤트를 lang 별 국기/색으로 렌더, badge 에 룸 언어.

## 테스트
- `tests/test_languages.py`: normalize(jp→ja, 중복/무효 제거, 빈→DEFAULT, str·list 수용), names, gladia_codes.
- `tests/test_coach.py`(또는 신규): `translate_multi` — FakeClient 가 JSON 반환 시 dict 파싱, 비JSON/예외 시 `{}`. build_multi_system_prompt 에 언어 이름 포함.
- `tests/test_meeting_session.py`: `_record_line` 새 shape, `_translate_bg` 가 dict 결과를 각 lang emit + entry 채움(FakeClient 주입), record() 에 languages.
- `tests/test_meeting_session.py`: MeetingSetup languages 단계(쉼표 파싱·기본).
- `tests/test_llm_summarize.py`: summarize(text, lang_name) 가 프롬프트에 lang_name 포함.
- 웹: `npm run typecheck` + app/viewer JS 구문 + 수동(렌더).

## 검증
- `.venv/bin/python -m pytest -q` 통과 + `import main, live_translate, coach, llm, languages`.
- `cd jarvis-web && npm run typecheck` 0, app.html/viewer.html JS 구문 OK.
- 수동(둘 다 머지 후 재시작/배포): 폼에서 ko·en·jp 선택 → 회의; ko 발화 → en·ja 자막 동시; ja 발화 → ko·en 자막; 콘솔 /meet 언어 단계; 헤더 badge 룸 언어; 종료 후 `meetings.db` summary 가 `{ko:..,en:..,ja:..}` JSON.

## 영향 파일
| 파일 | Phase | 변경 |
|---|---|---|
| `languages.py` (신규) | A | 코드 정규화/매핑 |
| `coach.py` | A | translate_multi + build_multi_system_prompt |
| `live_translate.py` | A | MeetingMeta.languages, _setup_translator, _translate_bg, _emit(lang), STT 언어, record/transcript, MeetingSetup 언어단계 |
| `llm.py` | A | summarize(text, lang_name) |
| `main.py` | A | meeting_start languages 파싱, _save_meeting 언어별 요약 |
| `relay_client.py` | A | emit(kind, text, lang) |
| `jarvis-web/src/types.ts` | B | translation kind + lang 필드 + languages |
| `jarvis-web/src/meeting_do.ts` | B | PUBLIC_KINDS translation |
| `jarvis-web/src/static/viewer.html` | B | 제네릭 translation 렌더 |
| `jarvis-web/src/static/app.html` | B | 폼 언어 체크박스 + translation 렌더 + badge |
| `tests/test_*` | A | languages/coach/session/summary 테스트 |

배포: **A·B 모두 머지 후** jarvis 재시작 + 웹 `wrangler deploy`(B 머지 시 자동). origin push 직접.
