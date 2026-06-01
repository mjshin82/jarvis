# Jarvis — 로컬 음성 비서 (최소 프로토타입)

```
'Hey Jarvis' → wake.wav → [발화 캡처] → ok.wav → STT → LLM(스트리밍) → TTS → 스피커
   (호출어 대기)                (VAD)                                       │
        ▲────────────────────  재생 완료 / 재생 중 'Hey Jarvis' 로 끼어들기 ─┘
```

- 호출어 **'Hey Jarvis'** 로 깨우는 구조 (openWakeWord, 로컬)
- STT(Moonshine)·TTS(Supertonic)·Wake(openWakeWord) 모두 **Mac 로컬(ONNX)**
- LLM 은 `mock`/`remote`(DeepSeek)/`local`(Ollama) 중 선택
- 호출어 상태머신이 **스피커 에코 되먹임 루프도 차단**(응답 중 에코는 무시, 진짜 호출어만 전환)

## 파일 구조

| 파일 | 역할 |
|------|------|
| `config.py`   | 오디오 포맷·모델·백엔드·호출어 등 전역 설정 |
| `audio_io.py` | 마이크 캡처 + VAD(발화) + 호출어 감지 훅 |
| `wake.py`     | openWakeWord 'Hey Jarvis' 감지 (로컬) |
| `stt.py`      | Moonshine 추론 (→ 텍스트) |
| `llm.py`      | LLM 백엔드(mock/remote/local) + **문장 단위 청킹** |
| `tts.py`      | Supertonic 추론 (→ 오디오, 로컬) |
| `player.py`   | 순서 보장 재생 + 효과음(`enqueue_file`) + barge-in `flush` |
| `main.py`     | 호출어 상태머신 오케스트레이터 |
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

## 구현된 것 / 남은 것

- ✅ **Wake word ('Hey Jarvis')**: 호출어로 깨우는 구조. 호출 전 음성은 무시.
- ✅ **Barge-in**: 응답 재생 중 'Hey Jarvis' 로 끊고 다시 듣기.
- ✅ **에코 루프 차단**: 호출어 상태머신으로 스피커 되먹임 무한호출 방지.
- ✅ **효과음**: 호출 인식(`wake.wav`) / 입력 완료(`ok.wav`). `scripts/make_fx.py` 로 생성, 교체 가능.
- ⬜ **한국어 호출어 '자비스'**: 현재 호출어는 영어 "Hey Jarvis"(사전학습). 한국어는 커스텀 학습 필요.
- ⬜ **LISTENING 타임아웃 튜닝, 멀티 호출어** 등.
- **합성↔재생 더 깊은 파이프라이닝**: 현재도 N 재생 중 N+1 합성이 겹치지만,
  문장 내부 청크 스트리밍까지 하려면 TTS 출력 스트리밍 필요.
