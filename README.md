# Jarvis — 로컬 음성 비서 + 회의 자막 중계

```
'Hey Jarvis' → wake.wav → [발화 캡처] → ok.wav → STT → LLM(스트리밍) → TTS → 스피커
   (호출어 대기)                (VAD)                                       │
        ▲────────────────────  재생 완료 / 재생 중 'Hey Jarvis' 로 끼어들기 ─┘

                          ╔════════════════════╗
                          ║ 콘솔 입력 (Claude Code 스타일)
                          ║   상단: 로그가 위로 흐름
                          ║   하단: 고정 입력 박스 + 슬래시 명령 자동완성
                          ╚════════════════════╝
```

- **음성 + 텍스트 동시 입력**. 화면 하단에 고정 입력 박스(prompt_toolkit), `/` 로 슬래시 명령 자동완성
- 호출어 **'Hey Jarvis'** + 텍스트 입력 둘 다 같은 LLM 파이프라인으로
- STT(faster-whisper) · TTS(Supertonic) · Wake(openWakeWord) 모두 **Mac 로컬**
- LLM 은 `mock` / `remote`(DeepSeek) / `local`(Ollama) 중 선택
- **다양한 모드**: 평상시 비서 · 영어 미팅 연습(시뮬레이션) · 양방향 번역(/trans) · 회의 실시간 자막+양방향 번역(/meet, 외부 viewer 공유)

## 파일 구조

| 파일 | 역할 |
|------|------|
| `config.py`   | 오디오 포맷·모델·백엔드·호출어·중계 등 전역 설정 |
| `audio_io.py` | 마이크 캡처 + VAD(발화) + pause/resume + 호출어 감지 훅 |
| `wake.py`     | openWakeWord 'Hey Jarvis' 감지 (로컬) |
| `stt.py`      | faster-whisper 추론. 모드별 언어/프롬프트 동적 분기 |
| `llm.py`      | LLM 백엔드(mock/remote/local) + 스트리밍 + 음악 fast-path |
| `tts.py`      | Supertonic 추론. text_norm 으로 숫자/단위 정규화 후 합성 |
| `text_norm.py`| TTS 직전 숫자/통화/단위/카운터 정규화 (한·영·일 자동 분기) |
| `player.py`   | 순서 보장 재생 + 효과음(`enqueue_file`) + barge-in `flush` |
| `console.py`  | prompt_toolkit Application — 고정 입력 박스, 스피너, 큐 표시, 자동완성 |
| `commands.py` | 슬래시 명령 디스패처 (`/bye /tts /mic /trans /stop /meet`) |
| `intents.py`  | 음악 명령 fast-path 분류기 (LLM 호출 0회로 즉시 실행) |
| `main.py`     | 호출어 상태머신 + 텍스트 입력 큐 + 모드 라우팅 오케스트레이터 |
| `wordbook.py` / `wordbook.txt` / `wordbook_meet.txt` | 고유명사 워드북(STT 컨디셔닝 + 사후치환 + LLM 힌트). 회의 전용 파일 분리 |
| `simulation.py` / `scenarios/*.md` | 시뮬레이션 모드 매니저 + 시나리오 페르소나 |
| `qa.py` / `scenarios/*.qa.md` | 시나리오 Q&A 뱅크 — 예상 질문·답변 쌍을 코드가 선택 |
| `coach.py`    | 시뮬·번역·회의 모드의 LLM 마이크로 작업 (평가, 번역, 페르소나 wording) |
| `live_translate.py` | 회의 모드(`/meet`) — RealtimeSTT + 양방향 번역 + listener fan-out |
| `relay_client.py` | 회의 자막을 meeting-web 서버로 보내는 outbound WebSocket publisher |
| `meeting-web/` | (서브프로젝트) Cloudflare Workers 자막 중계 서버 — 외부 viewer 공유 |
| `scripts/make_fx.py` | 효과음(`sound/fx/wake.wav`, `ok.wav`) 생성 |
| `scripts/wake_debug.py` | 마이크 레벨 + wake 점수 라이브 디버거 |
| `scripts/realtime_poc.py`, `scripts/wake_cpu_poc.py` | RealtimeSTT 평가 PoC |

## 설치

```bash
cd jarvis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # DEEPSEEK_API_KEY 등 채우기
```

> 모델(faster-whisper, Supertonic, openWakeWord)은 **첫 실행 시 HuggingFace에서 자동 다운로드**되어 캐시됩니다.

> Ollama 사용 시: `brew install --cask ollama-app` → `open -a Ollama` → `ollama pull qwen3:4b-instruct`

## 실행

```bash
python main.py
```

> 마이크 권한: 시스템 설정 → 개인정보 보호 및 보안 → 마이크 에서 사용 중인 터미널 앱 허용.

화면이 이렇게 흐릅니다:

```
🎙️  'Hey Jarvis' 라고 부르거나 아래에 텍스트를 입력하세요.
🧑 오늘 날씨 어때?
🤖 청량한 가을 날씨네요.

⠹ 생각 중…                       ← 진행 중 스피너
────────────────────────────────────────────────────────────
> /                              ← 고정 입력 박스 (밝은 시안 = 명령)
────────────────────────────────────────────────────────────
  /bye    프로그램 종료           ← '/' 누르면 자동완성 메뉴
  /meet   회의 모드 — 메타 입력 후 실시간 자막 + 양방향 번역
  /mic    듣기 모드로 전환 ('Hey Jarvis' 호출과 동일)
  /stop   현재 진행 모드(번역/회의) 종료
  /trans  번역 모드 — 발화를 한국어로 옮김 (/stop 까지)
  /tts    입력 문장을 그대로 읽기
```

## 콘솔/입력 키 바인딩

| 키 | 동작 |
|----|------|
| `Enter` | 제출 (자동완성 메뉴 떠있으면 선택 항목 채우기) |
| `Option+Enter` | 줄바꿈 (멀티라인) |
| `Option+←/→` | 단어 단위 이동 (Option Meta 설정 필요) |
| `Esc` | 메타 입력 취소 → 입력 박스 비움 → 진행 중 응답 취소 → 무동작 (단계적) |
| `Ctrl+C` | 입력 비우기 (빈 줄에서 한 번 더 → 종료) |
| `Ctrl+D` | 종료 |
| `↑ ↓` | 메뉴 떠있으면 선택 이동, 멀티라인이면 줄 이동, 그 외 히스토리 |

응답 진행 중에 입력하면 **큐에 쌓아두고 끝나면 처리**합니다(Claude Code 스타일). 큐는 항상 최신 1건만 유지 — 빠르게 여러 줄 치면 마지막 것만 처리.

## 슬래시 명령

| 명령 | 동작 |
|------|------|
| `/bye` | 프로그램 종료 |
| `/tts <문장>` | 받은 문장을 그대로 TTS로 읽기 (LLM 안 거침) |
| `/mic` | "Hey Jarvis" 호출과 동일 — 효과음 + 듣기 모드 진입 |
| `/trans [lang]` | 번역 모드 진입 — 발화를 한국어로 옮김. `/stop` 까지 무한 듣기 |
| `/meet` | 회의 모드 — 메타 입력 후 RealtimeSTT 실시간 자막 + 양방향 번역 |
| `/stop` | 진행 중 모드(번역/회의) 종료 |

명령 추가는 `commands.py` 에 `@command` 데코레이터 한 줄로 끝.

## 설정 (`.env`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_BACKEND`       | `mock` | `mock`(고정 메시지) / `remote`(DeepSeek) / `local`(Ollama) |
| `LOCAL_MODEL`       | `qwen3:4b-instruct` | local 모델 (Ollama 태그) |
| `OLLAMA_BASE_URL`   | `http://localhost:11434/v1` | local Ollama 엔드포인트 |
| `DEEPSEEK_API_KEY`  | (빈값) | remote/회의 번역에 사용 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | |
| `DEEPSEEK_MODEL`    | `deepseek-chat` | remote 모델 ID |
| `WHISPER_MODEL`     | `small` | STT 모델. 짧은 명령엔 small 균형 좋음 |
| `WHISPER_COMPUTE`   | `int8` | CPU 연산 정밀도 |
| `WHISPER_LANG`      | `ko` | 평상시 STT 언어 |
| `MIC_DEVICE`        | (빈값) | 마이크 장치 이름/인덱스. 비우면 자동(BlackHole 등 가상장치 회피) |
| `FOLLOW_UP`         | `true` | 답변 후 호출어 없이 바로 다시 듣기 |
| `LISTEN_TIMEOUT_S`  | `8.0` | 듣기 무발화 시 대기 복귀 |
| `SERPER_API_KEY`    | (빈값) | 웹 검색(구글/Serper) 키. 있으면 `web_search` 도구 활성 |
| `MUSIC_ENABLED`     | `true` | `play_music` / `stop_music` 도구. 발화엔 코드 fast-path |
| `BROWSER_APP`       | `Google Chrome` | 음악 재생에 쓸 브라우저(macOS 앱 이름) |
| `SUPERTONIC_VOICE`  | `F1` | 평상시 음성 (M1~M5 / F1~F5) |
| `SUPERTONIC_LANG`   | `ko` | 평상시 합성 언어 |
| `SUPERTONIC_SPEED`  | `1.05` | 말 속도 |
| `SUPERTONIC_STEPS`  | `8` | 합성 스텝(↓ 빠르고 품질↓) |
| `SIM_LANG_DEFAULT`  | `en` | 시뮬 모드 STT/TTS 언어 |
| `SIM_TTS_VOICE`     | `M1` | live 모드 음성 |
| `SIM_DEFAULT_SCENARIO` | `publisher_first_meeting` | 미지정 시 사용 |
| `MEET_REMOTE_ENABLED` | `true` | 회의 번역에 DeepSeek 사용 (없으면 본체 LLM 폴백) |
| `MEET_REMOTE_MODEL` | `deepseek-chat` | 회의 번역 모델 |
| `MEET_CONTEXT`      | (텍스트) | 회의 시스템 프롬프트 컨텍스트 |
| `RELAY_URL`         | (빈값) | 회의 자막 중계 ws URL (예: `wss://...workers.dev`) |
| `RELAY_TOKEN`       | (빈값) | meeting-web 의 RELAY_TOKEN 과 일치 |

## 워드북 (고유명사 인식 보정)

STT 가 자주 헛듣는 고유명사·전문용어를 `wordbook.txt` (평상시) / `wordbook_meet.txt` (회의 전용)에 등록하면 **3중 방어**로 보정:

| 단계 | 작동 |
|------|------|
| STT `initial_prompt` | faster-whisper 컨디셔닝 → 음향적으로 비슷한 후보 중 워드북 단어 선호 |
| STT 사후 치환 | `자비스` → `Jarvis` 같은 자주 틀리는 표기를 정식 표기로 자동 변환 |
| LLM 시스템 프롬프트 힌트 | 어휘 목록 노출 → STT 가 살짝 빗나가도 LLM 이 문맥상 추론 |

**형식** (한 줄에 하나):

```text
# 1) 정식 표기만
Jarvis
그레이테일

# 2) 정식=오인식1,오인식2  (좌변으로 자동 교정. 띄어쓰기/구두점 유연 매칭)
NVIDIA=엔비디아,너비디아,엔배디어
Graytail=그레이테일,그래이테일,Grarytail
```

회의 모드는 `wordbook_meet.txt` 를 별도로 읽어 LLM 시스템 프롬프트(DeepSeek)에 glossary 형태(`Concode (variants: 콩코드, 컨코드, ...)`)로도 주입 — 번역 시 정식 표기로 통일.

## 시뮬레이션 모드 (영어 미팅 연습)

`scenarios/<key>.md` 페르소나 + `scenarios/<key>.qa.md` Q&A 뱅크로 **3가지 연습 모드**:

| 모드 | 흐름 | 트리거 예시 |
|------|------|------|
| **guided** | 질문 + 영어 예시 답변 → 사용자 시도 → 한국어 코멘트 → "다시/예시/다음" 선택 | "답변 예시 보면서 연습" |
| **random** | 무작위 질문 → 사용자 답 → 짧은 한국어 코멘트 → 자동 다음 | "랜덤으로 질문해줘" |
| **live** | 인사~마무리 자유 대화, 영어 + 페르소나 유지 | "실전처럼 해보자", "롤플레이" |

"영어 연습하자"처럼 모호하게 진입하면 **3가지 모드를 한국어로 안내**하고 다음 발화에서 모드를 정함. "그만"으로 깨끗하게 종료.

Python 상태머신이 흐름을 통제하고 질문은 QA 뱅크에서 코드가 골라 그대로 출력. LLM 은 사용자 답에 대한 평가 / live 의 wording 변형 정도만 — 4B 로컬 모델에서도 시나리오 충실도 유지.

**제어 트리거**: `다시 / 예시 / 다음 / 그만`

## 번역 모드 (`/trans`)

발화를 한국어로 즉시 번역해 텍스트 출력 (TTS X).
- `/trans` → 자동 감지, `/trans en` / `/trans ja` 로 강제 가능
- `/stop` 까지 무한 듣기. 호출어/효과음 비활성 (연속 발화 흐름 유지)
- STT+번역(LLM) 통째로 백그라운드 — 다음 발화 안 끊김

## 회의 모드 (`/meet`) + 자막 중계

회의에서 상대(영어/일본어)와 나(한국어)의 발화를 **양방향 실시간 번역** + 외부 참석자가 브라우저로 자막을 함께 볼 수 있음.

### 진입 흐름

```
> /meet
🎤 회의 시작 전 정보를 입력해주세요. (Esc 로 취소)
   상대방 이름을 입력해주세요.
> Chucklefish
   상대방 사용 언어를 입력해주세요. (예: English, Japanese)
> English
   내 이름을 입력해주세요.
> Concode
   내 언어를 입력해주세요. (예: Korean)
> Korean
🎤 회의 모드 시작 (번역: DeepSeek (deepseek-chat)). 끝내려면 /stop.
🎤 회의를 시작합니다. 회의 번호: Chucklefish_Concode
🌐 중계 활성: http://localhost:8787/m/Chucklefish_Concode

🧑 Hello, thanks for having me.
🌐 안녕하세요, 만나 주셔서 감사합니다.

🧑 만나서 반갑습니다.
🇺🇸 Nice to meet you.

> /stop
🎤 회의 모드 종료.
```

### 특징

- **RealtimeSTT**: 발화 시작 시점부터 부분 결과 흐르고, 끝나면 small 모델로 확정. 회의 동안만 메모리 적재(평상시 본체 STT 와 분리)
- **양방향 자동 분기**: 한글 발화 → 영어로(🇺🇸), 그 외 → 한국어로(🌐)
- **DeepSeek 번역**: 회의용 시스템 프롬프트(컨텍스트 + 회의 워드북 glossary + few-shot)를 한 번 빌드해서 매 호출 동일하게 보내 **자동 prompt caching 히트** → 발화 100~200회 회의에 1시간 ~$0.05
- **외부 자막 중계**: `RELAY_URL` 설정되어 있으면 회의 시작 시 자동으로 [meeting-web](meeting-web/README.md) 으로 outbound WebSocket 연결. 참석자는 `http://<relay>/m/<회의키>` 에서 실시간 보기
- 키 없거나 비활성이면 자비스 본체 LLM 으로 자연 폴백

## meeting-web (자막 중계 서비스)

회의 자막을 외부 참석자가 브라우저로 볼 수 있게 하는 **서브프로젝트**. Cloudflare Workers + Durable Objects + Hono. 회의 키별 DO 1개 인스턴스로 메모리에 최근 100 이벤트 유지, viewer 들에 fan-out.

자세한 건 [meeting-web/README.md](meeting-web/README.md) 참고. 짧게:

```bash
cd meeting-web
npm install
cp .dev.vars.example .dev.vars   # RELAY_TOKEN 설정
npm run dev                       # localhost:8787 dev 서버
```

자비스 `.env` 에:
```
RELAY_URL=ws://localhost:8787
RELAY_TOKEN=devtoken
```

배포(`wrangler deploy`) 후 `RELAY_URL` 을 `wss://meeting-web-jarvis.<acct>.workers.dev` 로 바꾸면 외부 참석자 공유 가능.

## 구현된 것

- ✅ **Wake word ('Hey Jarvis')** + 호출어 상태머신으로 에코 루프 차단
- ✅ **Barge-in**: 응답 중 호출어/Esc 로 끊기
- ✅ **콘솔 UI** (Claude Code 스타일) — 고정 입력 박스, 스피너, 큐 표시, 슬래시 자동완성
- ✅ **텍스트 + 음성 동시 입력** — 같은 LLM 파이프라인. 응답 중 입력은 큐잉(최신 1건 유지)
- ✅ **슬래시 명령** + 명령 fast-path (음악)
- ✅ **TTS 정규화** — 숫자/통화/단위/카운터/시간 한·영·일 자동 분기
- ✅ **워드북** 3중 방어 (STT 컨디셔닝 + 사후치환 + LLM 힌트). 평상시/회의 워드북 분리
- ✅ **시뮬레이션 모드** 3가지 (guided/random/live) + QA 뱅크
- ✅ **번역 모드** (`/trans`) 양방향 자동 분기
- ✅ **회의 모드** (`/meet`) — 실시간 자막 + DeepSeek 양방향 번역 + 외부 자막 중계

## 남은 것 (V2 후보)

- ⬜ 한국어 호출어 '자비스' (현재는 영어 사전학습 모델)
- ⬜ 회의 자막 영구 보관 + 끝난 회의 재시청
- ⬜ 회의 viewer 인증/접근 제어
- ⬜ TTS 출력 스트리밍(문장 내부 청크) — 합성↔재생 더 깊은 파이프라이닝
