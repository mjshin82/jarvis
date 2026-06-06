# 회의 요약 개편 (언어별 텍스트 · 전용 모델 · 뷰어 라디오) 설계

날짜: 2026-06-07

## 목표
요약 품질을 바로잡는다.
1. 언어별 요약은 **그 언어의 텍스트만** 모아 LLM 에 전달(현재는 원문+모든 번역 혼합 텍스트를 모든 언어에 동일 투입 → 엉망).
2. 요약 전용 **상위 모델 + thinking**(`deepseek-v4-pro`), 대화·번역은 `deepseek-v4-flash` 로.
3. 자막 중계 페이지 요약 라디오에 **대화기록**도 포함(대화기록 / 언어별 요약 전환).
4. `e7766f` 회의 요약을 새 로직으로 수동 재생성.

## 비범위 (YAGNI)
- 요약 길이/스타일 옵션, thinking 강도(reasoning_effort) 노출.
- 소스언어를 LLM 으로 감지(아래처럼 룸언어−번역키로 도출).

---

## ① 언어별 텍스트 수집 (소스언어 LLM 없이 도출)
- 번역은 "소스 제외 나머지 룸 언어 전부"로 수행 → 한 줄의 `translations` 키에 **없는** 룸 언어가 그 줄의 **소스 언어**. `src_lang(line) = room_langs − translations.keys()`. 2언어 방은 항상 1개로 확정(LLM 불필요).
- **순수 함수**(테스트 용이)로 `meeting_store.py` 에 추가:
  ```python
  def lang_text(lines, lang, room_langs):
      """트랜스크립트에서 한 언어(lang)의 텍스트만 모아 합침.
      소스언어 = 그 줄의 translations 에 없는 룸 언어(번역 대상에서 제외된 것)."""
      out = []
      for e in (lines or []):
          tr = e.get("translations") or {}
          missing = [l for l in room_langs if l not in tr]   # 번역 안 된 룸 언어 = 소스
          src = missing[0] if len(missing) == 1 else None
          t = (e.get("source") or "") if lang == src else (tr.get(lang) or "")
          if t and t.strip():
              out.append(t)
      return "\n".join(out)
  ```
- `main._save_meeting`: 룸 언어마다 `text_lc = meeting_store.lang_text(lines, lc, room_langs)` → `llm.summarize(text_lc, NAMES[lc])`. (혼합 `_line_text` 폐기.)

## ② 요약 전용 상위 모델 + thinking
- `config.py`:
  - `DEEPSEEK_MODEL`: `deepseek-chat` → **`deepseek-v4-flash`** (대화).
  - `MEET_REMOTE_MODEL`: `deepseek-chat` → **`deepseek-v4-flash`** (번역).
  - 신규 `SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "deepseek-v4-pro")`.
- `llm.py` `summarize`: 요약 전용 타겟 사용.
  - `__init__`: `self._summary_client = None`.
  - `_summary_target()`: deepseek 키 유효 → 전용 `AsyncOpenAI(deepseek)`(lazy) + `config.SUMMARY_MODEL` + `extra_body={"thinking":{"type":"enabled"}}`; 키 없으면 `(self.client, self.model, self.extra)` 폴백(thinking 없음).
  - `summarize(text, lang_name)`: mock/빈 → ""; target client None → ""; `client.chat.completions.create(model, messages, extra_body=extra)` → `message.content`.
  - 시스템 프롬프트는 기존(회의록 페르소나 마크다운, 번역과 별개) 유지.

## ③ 뷰어 라디오: 대화기록 + 언어별 요약
- `viewer.html`: 상단 라디오 바 `#view-tabs` — **대화기록** + 요약 언어별(🇰🇷 한국어 요약, 🇺🇸 English 요약 …). 기본 대화기록.
  - 대화기록 선택 → `#log` 표시, `#summary-body` 숨김.
  - 언어 선택 → `#log` 숨김, `#summary-body` 에 그 언어 `marked.parse(summaries[lang])` 표시.
- `renderSummaries(summaries)` 가 탭 구성: 항상 "대화기록" 탭 + summaries 키별 탭. 요약 없으면 대화기록 탭만(또는 탭 바 숨김). 탭 변경 시 패널 전환.
- 기존 `#summary-langs`(요약 전용 라디오)·`#summary` 패널 구조를 이 통합 탭으로 대체. `#log` 와 요약 본문은 상호 배타 표시.

## ④ e7766f 수동 재요약
- 구현·머지 후 일회성: `meetings.db` 에서 e7766f transcript 로드 → 새 `_lang_text` 로 언어별 텍스트 → `llm.summarize`(v4-pro+thinking) → `{lang:summary}` JSON 으로 `store.set_summary("e7766f", ...)`. (jarvis 실행 환경에서 스크립트 실행 — deepseek 키 필요.)

## 테스트
- `tests/test_llm_summarize.py`: deepseek 키 있을 때 `_summary_client`(주입) + `SUMMARY_MODEL` + thinking extra 로 호출; 키 없을 때 self.client 폴백. mock → "".
- `meeting_store.lang_text`: 2언어 트랜스크립트(ko 소스 줄 + en 소스 줄)에서 lang="ko"/"en" 각각 그 언어 텍스트만 모음 검증.
- 웹: `npm run typecheck`(types 무변경) + viewer.html JS 구문. 수동: 대화기록↔요약 라디오 전환, 언어별 요약이 해당 언어만 담김.

## 검증
- `.venv/bin/python -c "import config, llm, main"` + `pytest -q`.
- viewer.html JS 구문 OK. 수동(배포·재시작): 새 회의 요약이 언어별로 정확, 라디오에 대화기록 포함, e7766f 요약 갱신.

## 영향 파일
| 파일 | 변경 |
|---|---|
| `config.py` | DEEPSEEK_MODEL/MEET_REMOTE_MODEL → v4-flash, SUMMARY_MODEL(v4-pro) |
| `llm.py` | summarize 전용 클라이언트(v4-pro)+thinking, `_summary_target` |
| `meeting_store.py` | `lang_text(lines, lang, room_langs)` 순수 헬퍼 |
| `main.py` | `_save_meeting` 가 언어별 `lang_text` 로 요약 |
| `jarvis-web/src/static/viewer.html` | 대화기록+요약 통합 라디오 탭 |
| `tests/test_*` | summarize 모델/thinking, _lang_text |
| (일회성) | e7766f 재요약 스크립트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
