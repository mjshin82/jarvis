# 음성 모드 전환 (서브프로젝트 C) 설계

날짜: 2026-06-05

> 상위 비전: jarvis 범용 웹 컨트롤러. 분해 A(골격)·B(채팅 홈) 완료 → **C(음성 모드 전환)**.
> 이 문서는 C 만 다룬다.

## 목표

음성/텍스트로 "미팅모드로 변경해줘" 같은 말을 하면 jarvis 가 회의 모드(`/meet`)로
전환하고, 웹 프론트(폰)는 자동으로 `/{name}/meeting` 으로 이동한다. 반대로 "회의
끝내줘"면 회의 종료 + 홈(`/{name}`)으로 복귀.

## 접근 (의도 인식)

LLM 은 이미 tool-calling 을 지원하지만(web_search/music), 모드 전환 동작은 `main()` 의
회의 클로저(`start_meeting_setup`/`stop_meeting`)에 있어 LLM 툴로 빼면 결합도가 커진다.
그래서 **STT/입력 텍스트에 대한 키워드 의도 매칭을 LLM 앞단에서** 한다. 단순·결정적·
백엔드 무관하며 토이에 견고하다.

## 의도 매칭

`main()` 안에 순수 헬퍼 `_mode_intent(text) -> "meeting" | "stop" | None`:
- **"meeting"**: 텍스트에 회의-명사(`회의`/`미팅`/`meeting`)가 있고 전환-동사(`전환`/`변경`/
  `시작`/`들어가`/`열어`/`켜`/`바꿔`) 중 하나가 있을 때.
- **"stop"**: 회의-명사 + 종료-동사(`끝`/`종료`/`나가`/`중지`/`꺼`) 중 하나.
- 그 외 `None` (일반 대화 → LLM). 오탐을 줄이려 명사+동사 **둘 다** 요구.

순수 함수라 단위 테스트로 케이스를 고정한다.

## 데이터 흐름

```
음성 → STT → respond_flow_audio: text
  intent = _mode_intent(text)
  intent == "meeting":
    web_pub.emit("assistant", "🎤 회의 모드로 전환합니다")   # 폰 채팅에 표시
    await start_meeting_setup()                              # 회의 시작(동적 마이크)
    web_pub.emit("navigate", "meeting")                      # 폰: location → /{name}/meeting
    (LLM 호출 안 함)
  intent == "stop":
    await stop_meeting()
    web_pub.emit("navigate", "home")                         # 폰: location → /{name}
  intent is None:
    await speak_response(text)                               # 기존 대화
```
텍스트 입력(`respond_flow_text`)도 비슬래시 경로에서 동일하게 `_mode_intent` 검사.

웹: `/subscribe` 로 받은 `{kind:"navigate", text:"meeting"|"home"}` →
`location.href = "/<name>"` 또는 `"/<name>/meeting"`.

## 컴포넌트 변경

**`main.py`:**
- `_mode_intent(text)` 헬퍼 추가(순수 함수, 모듈 또는 main 내부 — 테스트 위해 모듈 레벨 권장).
- `respond_flow_audio`: STT 후 `text` 가 있으면 먼저 `_mode_intent` 검사. "meeting"/"stop"
  이면 위 동작 실행(LLM/speak_response 생략) 후 평소처럼 FOLLOW_UP/idle 마무리.
- `respond_flow_text`: 슬래시 아님 + `_mode_intent` 매칭 시 동일 동작, 아니면 speak_response.
- 동작 헬퍼 `enter_meeting_voice()` / `exit_meeting_voice()` (또는 인라인): start/stop +
  `web_pub.emit("navigate", ...)`(web_pub 있을 때).
- 이미 회의 중 "meeting" → `start_meeting_setup` 가 "이미 진행 중" 처리(그대로).
  회의 아닌데 "stop" → `stop_meeting` 가 "회의 모드가 아닙니다" 처리(그대로). navigate 는
  상태가 실제 바뀐 경우에만 보내도 되지만, 단순화를 위해 의도대로 보냄(웹은 이미 그 페이지면 무해).

**`jarvis-web/src/types.ts`:** `EventKind` 에 `"navigate"` 추가.

**`jarvis-web/src/static/home.html`:** `/subscribe` 메시지 핸들러에 `navigate` 분기:
`ev.kind === "navigate"` → `if (ev.text === "meeting") location.href = "/" + encodeURIComponent(name) + "/meeting";`
(이미 홈이면 home 으로의 navigate 는 무시/무해.)

**`jarvis-web/src/static/meeting.html`:** 동일 핸들러에 `navigate` 분기 →
`ev.text === "home"` 면 `location.href = "/" + encodeURIComponent(key);`. (회의 종료 시 폰을
홈으로 되돌림. `key` 는 이 페이지의 name.)

## 엣지 케이스

- **오탐:** "회의 자료 정리해줘"는 회의-명사만 있고 전환/종료-동사 없음 → None → 일반 대화.
  명사+동사 동시 요구로 완화. 완벽하진 않으나 토이 충분(필요 시 패턴 보강).
- **web_pub 미설정(RELAY 없음):** navigate emit 무시(가드), 회의 전환은 로컬에서 정상 동작.
- **이미 해당 모드:** start/stop 의 기존 가드가 처리. navigate 는 동일 페이지로 가도 무해.
- **회의 진입 중 마이크:** 동적 회의(B 이전 작업)가 현재 활성 소스를 따르므로, 폰이 mic
  take 중이면 회의도 폰 음성을 씀. 별도 처리 불필요.

## 테스트 전략

- **단위(`_mode_intent`):** "미팅모드로 변경해줘"/"회의 모드 시작"/"회의 들어가자" → "meeting";
  "회의 끝내줘"/"회의 종료"/"회의 나가자" → "stop"; "오늘 회의 자료 요약"/"안녕" → None.
  (순수 함수 — 모듈 레벨로 빼서 import 후 테스트.)
- **통합(jarvis-web):** `mic_relay_check.mjs` 에 publisher 가 `{kind:"navigate",text:"meeting"}`
  emit → viewer 수신 검증(텍스트 이벤트라 기존 JSON 경로) — 또는 단순히 navigate kind 가
  브로드캐스트되는지.
- **수동 E2E:** 폰 홈 → mic-take → "미팅 모드로 변경해줘" → 폰이 `/Concode/meeting` 으로 이동 +
  jarvis 회의 시작. "회의 끝내줘" → 홈 복귀 + 회의 종료.

## 연기 / 비범위

- LLM tool 기반 의도(현 키워드로 충분). 다른 모드(번역 등) 음성 전환은 추후 동일 패턴으로 확장 가능.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `main.py` (또는 신규 `intent.py`) | `_mode_intent` + respond_flow_audio/text 의도 분기 + navigate emit |
| `jarvis-web/src/types.ts` | `navigate` kind |
| `jarvis-web/src/static/home.html` | navigate → /{name}/meeting |
| `jarvis-web/src/static/meeting.html` | navigate(home) → /{name} |
| `tests/` | `_mode_intent` 케이스 |
