# Jarvis — 로컬 음성 비서 (최소 프로토타입)

```
마이크 → VAD(silero) → STT(Moonshine, 로컬) → LLM(DeepSeek V4 API) → TTS(Supertonic, 로컬) → 스피커
```

온라인 의존은 **LLM 한 곳뿐**. STT·TTS 는 Mac 로컬(ONNX) 실행.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `config.py`   | 샘플레이트·모델명·API 키 등 전역 설정 |
| `audio_io.py` | 마이크 캡처 + VAD 로 발화 단위 잘라내기 |
| `stt.py`      | Moonshine 추론 (→ 텍스트) |
| `llm.py`      | DeepSeek 스트리밍 + **문장 단위 청킹** |
| `tts.py`      | Supertonic 추론 (→ 오디오, 로컬) ✅ 연결됨 |
| `player.py`   | 순서 보장 재생 (합성과 재생 분리) |
| `main.py`     | asyncio 오케스트레이터 |

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

- ✅ **Barge-in (말 끊기)**: AI 가 말하는 중 사용자가 말을 시작하면 즉시 중단.
  에코 완화(재생 중 VAD 임계값 상향 + 최소 지속시간 게이트) 포함.
- ⬜ **Wake word**: 항상 듣는 구조. "자비스" 호출어가 필요하면 `openWakeWord` 추가.
- **합성↔재생 더 깊은 파이프라이닝**: 현재도 N 재생 중 N+1 합성이 겹치지만,
  문장 내부 청크 스트리밍까지 하려면 TTS 출력 스트리밍 필요.
