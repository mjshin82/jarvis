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
| `DEEPSEEK_MODEL`    | `deepseek-v4-flash` | LLM 모델 ID |
| `SUPERTONIC_VOICE`  | `F1` | 음성 (M1~M5 / F1~F5) |
| `SUPERTONIC_LANG`   | `ko` | 합성 언어 |
| `SUPERTONIC_STEPS`  | `8`  | ↓ 줄이면 더 빠름(품질↓) |
| `SUPERTONIC_SPEED`  | `1.05` | 말 속도 |

## 이 골격이 일부러 빼놓은 것 (다음 단계)

- **Barge-in (말 끊기)**: 지금은 발화→응답을 순차 처리. AI 가 말하는 중
  사용자가 끼어들면 멈추는 기능은 없음. 필요해지면 재생 중 마이크 모니터링 +
  `sd.stop()` + 재생 큐 비우기로 구현하거나 `pipecat` 으로 이전.
- **Wake word**: 항상 듣는 구조. "자비스" 호출어가 필요하면 `openWakeWord` 추가.
- **합성↔재생 더 깊은 파이프라이닝**: 현재도 N 재생 중 N+1 합성이 겹치지만,
  문장 내부 청크 스트리밍까지 하려면 TTS 출력 스트리밍 필요.
