# Jarvis — 로컬 음성 비서 (최소 프로토타입)

```
'Hey Jarvis' → wake.wav → [발화 캡처] → ok.wav → STT → LLM(스트리밍) → TTS → 스피커
   (호출어 대기)                (VAD)                                       │
        ▲────────────────────  재생 완료 / 재생 중 'Hey Jarvis' 로 끼어들기 ─┘
```

- 호출어 **'Hey Jarvis'** 로 깨우는 구조 (openWakeWord, 로컬)
- STT(faster-whisper)·TTS(Supertonic)·Wake(openWakeWord) 모두 **Mac 로컬**
- LLM 은 `mock`/`remote`(DeepSeek)/`local`(Ollama) 중 선택
- 호출어 상태머신이 **스피커 에코 되먹임 루프도 차단**(응답 중 에코는 무시, 진짜 호출어만 전환)

## 파일 구조

| 파일 | 역할 |
|------|------|
| `config.py`   | 오디오 포맷·모델·백엔드·호출어 등 전역 설정 |
| `audio_io.py` | 마이크 캡처 + VAD(발화) + 호출어 감지 훅 |
| `wake.py`     | openWakeWord 'Hey Jarvis' 감지 (로컬) |
| `stt.py`      | faster-whisper 추론 (large-v3-turbo, 한국어, → 텍스트) |
| `llm.py`      | LLM 백엔드(mock/remote/local) + **문장 단위 청킹** |
| `tts.py`      | Supertonic 추론 (→ 오디오, 로컬) |
| `player.py`   | 순서 보장 재생 + 효과음(`enqueue_file`) + barge-in `flush` |
| `main.py`     | 호출어 상태머신 오케스트레이터 |
| `wordbook.py` / `wordbook.txt` | 고유명사 워드북(STT 컨디셔닝 + 사후치환 + LLM 힌트) |
| `simulation.py` / `scenarios/*.md` | 시뮬레이션(롤플레이) 모드 매니저 + 시나리오 |
| `scripts/make_fx.py` | 효과음(`sound/fx/wake.wav`, `ok.wav`) 생성 |

## 설치

```bash
cd /Users/oracle/Documents/concode/jarvis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # DEEPSEEK_API_KEY 채우기
```

> Supertonic 모델(~Supertone/supertonic-3)은 **첫 실행 시 HuggingFace에서 자동 다운로드**되어 캐시됩니다.

## 실행

```bash
python main.py
```

> 마이크 권한: 시스템 설정 → 개인정보 보호 및 보안 → 마이크 에서 터미널 허용.

## 설정 (`.env`)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_BACKEND`       | `mock` | `mock`(고정 메시지·비용0) / `remote`(DeepSeek) / `local`(Ollama) |
| `DEEPSEEK_MODEL`    | `deepseek-v4-flash` | remote 모델 ID |
| `OLLAMA_BASE_URL`   | `http://localhost:11434/v1` | local Ollama 엔드포인트 |
| `LOCAL_MODEL`       | `gemma4:e4b` | local 모델 (Ollama 태그) |
| `WHISPER_MODEL`     | `small` | STT 모델. 더 빠르게 `base`, 품질↑ `large-v3-turbo` |
| `WHISPER_COMPUTE`   | `int8` | CPU 연산 정밀도 |
| `WHISPER_LANG`      | `ko` | STT 언어 고정 |
| `FOLLOW_UP`         | `true` | 답변 후 호출어 없이 바로 다시 듣기(연속 대화) |
| `LISTEN_TIMEOUT_S`  | `8.0` | 듣기 상태에서 무발화 시 호출어 대기로 복귀 |
| `SERPER_API_KEY`    | (빈값) | 웹 검색(구글/Serper) 키. 있으면 `web_search` 도구 활성화 |
| `MUSIC_ENABLED`     | `true` | `play_music` 도구(유튜브 검색→브라우저 재생) |
| `BROWSER_APP`       | `Google Chrome` | 음악 재생에 쓸 브라우저(macOS 앱 이름) |
| `SUPERTONIC_VOICE`  | `F1` | 음성 (M1~M5 / F1~F5) |
| `SUPERTONIC_LANG`   | `ko` | 합성 언어 |
| `SUPERTONIC_STEPS`  | `8`  | ↓ 줄이면 더 빠름(품질↓) |
| `SUPERTONIC_SPEED`  | `1.05` | 말 속도 |

### LLM 백엔드별 실행

```bash
# mock (기본) — 항상 "AI를 통한 응답은 현재 mock처리됩니다."
LLM_BACKEND=mock python main.py

# local — Ollama 로컬 모델 (먼저: ollama serve / ollama pull gemma4:e4b)
LLM_BACKEND=local python main.py

# remote — DeepSeek API (.env 에 DEEPSEEK_API_KEY 필요)
LLM_BACKEND=remote python main.py
```

## 워드북 (고유명사 인식 보정)

STT 가 자주 헛듣는 고유명사·전문용어를 `wordbook.txt` 에 등록하면, **3중 방어**로
인식·표기·이해가 함께 보정된다. 코드 수정 없이 텍스트 파일만 편집하면 된다.

| 단계 | 작동 |
|------|------|
| STT `initial_prompt` | faster-whisper 에 어휘 컨디셔닝 → 음향적으로 비슷한 후보 중 워드북 단어 선호 |
| STT 사후 치환 | `자비스` → `Jarvis` 같은 자주 틀리는 표기를 정식 표기로 자동 변환 |
| LLM 시스템 프롬프트 힌트 | 어휘 목록을 노출 → STT 가 살짝 빗나가도 LLM 이 문맥상 추론 |

**형식** (`wordbook.txt`, 한 줄에 하나):

```text
# 주석은 '#' 으로 시작, 빈 줄 무시

# 1) 정식 표기만
Jarvis
그레이테일

# 2) 정식=오인식1,오인식2  (좌변으로 자동 교정)
NVIDIA=엔비디아,너비디아,엔배디어
Ollama=올라마,오라마
```

**팁**
- 영문 단어는 한글 음역도 같이 적기 (`Qwen=퀀,큐원`) — 한국어 발화는 보통 한글로 잡힘.
- `initial_prompt` 은 240자에서 잘림(너무 길면 Whisper 환각↑). 중요한 것부터 위에.
- LLM 시스템 프롬프트에는 상위 40개만 노출(프롬프트 비대화 방지).
- 파일 수정 후 `python main.py` 재시작 필요.

## 시뮬레이션 모드 (영어 미팅 연습 등)

`scenarios/<key>.md` 페르소나 시나리오로 **3가지 연습 모드** 중 하나에 진입.
진입하면 STT/TTS/시스템 프롬프트가 시나리오 언어로 자동 전환, 종료하면 평소 비서로 복귀.

| 모드 | 흐름 | 트리거 예시 |
|------|------|------|
| **guided** (가이드 연습) | 질문 + 영어 예시 답변 → 사용자 시도 → 한국어 코멘트 → "다시/예시/다음" 선택 | "답변 예시 보면서 연습", "가이드 받으면서 연습" |
| **random** (랜덤 질문) | 무작위 질문 → 사용자 답 → 짧은 한국어 코멘트 → 다음 무작위 질문 | "랜덤으로 질문해줘", "무작위로 물어봐" |
| **live** (실전 시뮬) | 인사~마무리 자유 대화, 100% 영어, 페르소나 유지 | "실전처럼 해보자", "롤플레이 해줘" |

**guided/random 의 진행은 Python 상태머신이 통제**한다 — LLM 은 "질문 + 예시 생성", "사용자 답 평가" 두 가지 마이크로 작업만 한다. 모델 크기에 덜 민감하고 흐름이 견고하다. live 만 LLM 이 페르소나로 자유 진행.

**종료/이동 트리거 (guided/random 어느 상태에서나)**
- `다시 / 한 번 더` — 같은 질문 다시 시도
- `예시 / 예시 다시 들려줘` — 예시 답변 재출력 후 다시 시도
- `다음 / 다음 질문 / next` — 새 질문으로 이동
- `그만 / 종료 / stop / quit` — 시뮬레이션 종료

영어 시도는 키워드와 겹쳐도 길이(≥30자)·문장 패턴으로 자동 구분됨.

**시나리오 추가**: `scenarios/<key>.md` 새로 만들고 첫 줄에 `# 제목`, 본문에 페르소나·맥락·다룰 토픽을 자유 형식으로. 3가지 모드 모두 자동으로 적용됨.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SIM_ENABLED` | `true` | 시뮬 도구 활성/비활성 |
| `SIM_LANG_DEFAULT` | `en` | 시뮬 모드 STT/TTS 언어 |
| `SIM_TTS_VOICE` | `M1` | live 모드 음성(코치 모드는 평상시 음성 사용) |
| `SIM_DEFAULT_SCENARIO` | `publisher_first_meeting` | 사용자가 시나리오 미지정 시 사용 |

## 구현된 것 / 남은 것

- ✅ **Wake word ('Hey Jarvis')**: 호출어로 깨우는 구조. 호출 전 음성은 무시.
- ✅ **Barge-in**: 응답 재생 중 'Hey Jarvis' 로 끊고 다시 듣기.
- ✅ **에코 루프 차단**: 호출어 상태머신으로 스피커 되먹임 무한호출 방지.
- ✅ **효과음**: 호출 인식(`wake.wav`) / 입력 완료(`ok.wav`). `scripts/make_fx.py` 로 생성, 교체 가능.
- ✅ **워드북**: 고유명사 인식 보정 — `wordbook.txt` 편집만으로 STT 컨디셔닝 + 사후치환 + LLM 힌트 동시 적용.
- ⬜ **한국어 호출어 '자비스'**: 현재 호출어는 영어 "Hey Jarvis"(사전학습). 한국어는 커스텀 학습 필요.
- ⬜ **LISTENING 타임아웃 튜닝, 멀티 호출어** 등.
- **합성↔재생 더 깊은 파이프라이닝**: 현재도 N 재생 중 N+1 합성이 겹치지만,
  문장 내부 청크 스트리밍까지 하려면 TTS 출력 스트리밍 필요.
