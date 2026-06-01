"""전역 설정. 오디오 포맷은 파이프라인 전체가 공유하므로 한 곳에 모은다."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- 오디오 포맷 (모든 모듈이 이 값을 공유) ---
SAMPLE_RATE = 16_000      # Moonshine / silero-vad 둘 다 16kHz 모노 기준
CHANNELS = 1
BLOCK_SIZE = 512          # silero-vad 권장 프레임 크기(16kHz에서 32ms)

# --- VAD ---
VAD_THRESHOLD = 0.5       # 평상시 음성 확률 임계값
SILENCE_MS = 700          # 이만큼 조용하면 "발화 끝"으로 간주

# --- 에코 대책 ---
# True(스피커 사용): 자비스가 말하는 동안 마이크 입력을 무시(반이중) → 에코 루프 차단.
#                    대신 재생 중 barge-in 불가 (호출어 층을 얹으면 끼어들기 가능).
# False(헤드폰 사용): 에코 경로가 없으므로 재생 중에도 즉시 barge-in 허용.
HALF_DUPLEX = os.getenv("HALF_DUPLEX", "true").lower() in ("1", "true", "yes")

# --- STT ---
MOONSHINE_MODEL = "moonshine/base"   # or "moonshine/tiny" (더 빠름)

# --- LLM 백엔드 선택: mock | remote | local ---
#   mock   : 실제 호출 없이 고정 메시지 응답 (비용 0, 기본값)
#   remote : DeepSeek V4 API (유료)
#   local  : 로컬 Ollama (OpenAI 호환 서버, 비용 0)
LLM_BACKEND = os.getenv("LLM_BACKEND", "mock").lower()
MOCK_MESSAGE = "AI를 통한 응답은 현재 mock처리됩니다."

# remote: DeepSeek (mock 모드에선 키 없어도 동작하도록 getenv)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# local: Ollama. 서버 실행 `ollama serve`, 모델 준비 `ollama pull gemma4:e4b`
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "gemma4:e4b")

SYSTEM_PROMPT = (
    "너는 'Jarvis'라는 음성 비서다. 사용자의 말을 듣고 간결하고 자연스럽게 "
    "대답한다. 답변은 소리내어 읽힐 것이므로 짧은 문장으로, 마크다운 기호 없이 말한다."
)

# --- TTS (Supertonic, 로컬) ---
SUPERTONIC_MODEL = os.getenv("SUPERTONIC_MODEL", "supertonic-3")
SUPERTONIC_VOICE = os.getenv("SUPERTONIC_VOICE", "F1")   # M1~M5 / F1~F5
SUPERTONIC_LANG = os.getenv("SUPERTONIC_LANG", "ko")     # 한국어 지원
SUPERTONIC_SPEED = float(os.getenv("SUPERTONIC_SPEED", "1.05"))
SUPERTONIC_STEPS = int(os.getenv("SUPERTONIC_STEPS", "8"))  # ↓줄이면 빠르고 품질↓
