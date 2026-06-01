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

# --- 에코 완화 (barge-in 오작동 방지) ---
# 자비스가 말하는 동안엔 스피커 소리(에코)가 마이크로 새어들 수 있다.
VAD_THRESHOLD_SPEAKING = 0.85  # 재생 중엔 임계값을 높여 약한 에코를 무시
BARGE_IN_MIN_MS = 300          # 재생 중엔 이만큼 '연속' 발화해야 barge-in 인정(짧은 에코 blip 무시)

# --- STT ---
MOONSHINE_MODEL = "moonshine/base"   # or "moonshine/tiny" (더 빠름)

# --- LLM (DeepSeek V4) ---
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

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
