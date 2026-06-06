# 회의 요약 개선 (프롬프트 + 표시) 설계

날짜: 2026-06-06

## 목표
1. **요약 프롬프트**: 요약자 페르소나 + 마크다운 출력(newsplease `SUMMARY_SYSTEM_PROMPT` 스타일 참고). 출력 언어는 `lang_name`.
2. **요약 표시(viewer.html)**: 요약 박스 폭을 대화 버블과 맞추고, 모든 언어를 한꺼번에가 아니라 **라디오로 하나씩** 전환, **marked.js**(벤도링)로 마크다운 렌더.

데이터 흐름은 변경 없음 — jarvis 가 언어별 마크다운 요약을 `summary` 컬럼 `{lang:markdown}` 으로 저장하고, `meeting_summary`/`archive_response` 로 viewer 에 전달.

## 비범위 (YAGNI)
- owner 앱(app.html)의 요약 표시(종료 시 홈으로 가므로 — viewer.html 만 대상).
- 마크다운 고급(수식/다이어그램). marked 기본 + GFM(표) 까지.
- 요약 길이/스타일 사용자 설정.

---

## ① 요약 프롬프트 (llm.py)

`LLM.summarize(self, text, lang_name="Korean")` 의 시스템 프롬프트를 교체. 페르소나 + 마크다운 규칙(newsplease 차용), 출력은 `{lang_name}`:

```python
        messages = [
            {"role": "system", "content": (
                f"You are an expert meeting-minutes writer. Summarize the meeting "
                f"conversation below clearly and faithfully, written ENTIRELY in {lang_name}.\n\n"
                "Format as GitHub-flavored Markdown:\n"
                "- Use ## (h2) for sections; put a --- divider line before each ## except the first. "
                "Use ### (h3) for subsections. Never use #### or deeper.\n"
                "- Use bullet points and **bold** for emphasis. Do not put quotes inside bold "
                "(write **text**, not **\"text\"**).\n"
                "- Use a Markdown table (| ... |) when comparing figures or itemized attributes.\n"
                "- For number ranges use a hyphen (2015-2020, not 2015~2020).\n\n"
                "Cover: key discussion points, decisions made, and action items (owner + task). "
                "Never invent information not in the conversation. "
                "Output ONLY the Markdown summary — no preamble, no code fences."
            )},
            {"role": "user", "content": text},
        ]
```
(mock/빈 입력 → "" 가드 기존 유지. 호출부·언어별 N회·저장은 변경 없음.)

테스트(`tests/test_llm_summarize.py`): `summarize(text, "Japanese")` 시스템 프롬프트에 `Japanese` + "Markdown" 포함; user 메시지에 본문 포함.

---

## ② 요약 표시 (viewer.html)

### marked 벤도링
- `marked.min.js`(UMD, ~30KB)를 viewer.html 에 **인라인 `<script>`** 로 임베드(`<head>` 또는 IIFE 앞). 전역 `marked` 노출 → `marked.parse(md)`.
- 소스 확보: jarvis-web 에 `marked` 설치 후 `node_modules/marked/marked.min.js`(또는 `lib/marked.umd.js`) 내용을 복사해 인라인. (npm 가용 시.)

### 폭 맞춤
- `#summary` CSS 를 `#log` 와 동일 컬럼으로: `max-width: 980px; margin: 0 auto; width: 100%;` (+ 좌우 패딩). 현재 전체폭 → 대화 버블과 정렬.

### 라디오 + 단일 언어 렌더
- `#summary` 구조: `<div id="summary-langs"></div>`(라디오 행) + `<div id="summary-body" class="md"></div>`(렌더 결과).
- `renderSummaries(summaries)`:
  - summaries 비면 `#summary` 숨김.
  - 언어별 라디오 생성(국기+이름: ko 🇰🇷 한국어, en 🇺🇸 English, ja 🇯🇵 日本語, zh 🇨🇳 中文). 첫 언어 `checked`.
  - 라디오 `change` → 선택 언어의 `marked.parse(summaries[lang])` 를 `#summary-body.innerHTML` 에. 단일 언어만 표시.
  - 모듈 변수 `_summaries` 보관(라디오 변경 시 재렌더; 새 summaries 도착 시 갱신, 현재 선택 언어 유지하되 없으면 첫 언어).
  - `#summary` 보이기.
- `meeting_archive`(초기)·`meeting_summary`(준비되면) 둘 다 `renderSummaries` 호출(기존 흐름).

### CSS (마크다운 본문 `.md`)
- 제목(h2/h3) 여백·크기, 불릿/번호 리스트 들여쓰기, `--- → <hr>`, 표 보더(`table/th/td` border-collapse), `code`/`pre` 배경, `strong` bold. 다크/라이트 var 사용.
- 라디오 행: 가로 배치, 작은 폰트.

---

## 데이터 모델 참고
- `summary` 컬럼 = `{lang: markdown}` JSON(언어별 마크다운). 트랜스크립트·요약 생성·저장·`meeting_summary`/`archive_response` 경로 모두 기존 유지.
- viewer 의 `meeting_archive {title, transcript, summaries}` / `meeting_summary {mid, summaries}` 의 `summaries` 가 곧 마크다운 dict.

## 테스트
- jarvis: `tests/test_llm_summarize.py` — 프롬프트에 lang_name + Markdown 지시 포함.
- 웹: `npm run typecheck`(types 무변경), viewer.html JS 구문(`node --check` 패턴) — marked 임베드 후에도 구문 OK. 수동: 종료 회의 입장 → 요약 라디오 전환·마크다운 렌더·대화 폭과 정렬.

## 검증
- `.venv/bin/python -m pytest -q` 통과.
- viewer.html `<script>` 구문 OK(marked 포함). `cd jarvis-web && npm run typecheck` 0.
- 수동(배포·재시작 후): 회의 종료 → 자막 링크 입장 → 요약이 마크다운(제목/불릿/표)으로, 언어 라디오로 전환, 박스 폭이 대화와 동일.

## 영향 파일
| 파일 | 변경 |
|---|---|
| `llm.py` | summarize 시스템 프롬프트(페르소나+마크다운) |
| `jarvis-web/src/static/viewer.html` | marked 인라인 임베드 + #summary 폭/라디오/마크다운 렌더 + CSS |
| `tests/test_llm_summarize.py` | 프롬프트 검증 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
