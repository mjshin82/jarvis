"""시뮬레이션 모드 매니저 (영어 미팅 연습 등).

전역 단일 인스턴스(`MODE`)가 현재 모드 상태를 들고 있고, STT/TTS/LLM 이 매 호출마다
이 상태를 읽어 동작 언어와 시스템 프롬프트를 결정한다. 모드 진입/탈출은 LLM 의
`start_simulation` / `end_simulation` 도구로 토글된다.

세 가지 연습 방식(practice mode):
  - guided : 질문 → 답변 예시 → 사용자 시도 → 피드백(한국어) → 다음 행동 선택
  - random : 시나리오 토픽 중 무작위 질문 → 사용자 답 → 짧은 피드백
  - live   : 실제 미팅처럼 인사~마무리까지 자유 대화(페르소나 100%)

시나리오 파일: scenarios/<name>.md (페르소나/맥락만 적음. 모드별 진행 지침은 코드에서 덧붙임)
  - 첫 줄 '# 제목' → 시나리오 이름
  - 본문 전체가 LLM 시스템 프롬프트(페르소나·규칙)에 들어감
"""
import os
from dataclasses import dataclass

import config

_SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")

PRACTICE_MODES = ("guided", "random", "live")

# live 모드만 LLM 자유 진행이라 시스템 프롬프트 부록이 필요. guided/random 은 코드가
# 상태머신으로 흐름을 통제하고, LLM 은 마이크로 작업(질문 생성·평가) 단위로만 호출.
_MODE_PROMPTS = {
    "guided": """

# Practice format: GUIDED (질문 + 예시 → 사용자 시도 → 짧은 코멘트 + 다음 안내)

⚠️ You are a PRACTICE COACH. NOT the publisher. You do not chat with the user as a real person — you coach them on how to answer publisher questions.

Each user message is one of two things only:
  (a) a practice attempt at the question you just asked, or
  (b) a navigation choice ("다시" / "예시" / "다음" / "그만").
Never treat user input as small talk that deserves a follow-up publisher question.

Each round happens across exactly TWO of your turns:

**TURN A — Present the round (all 3 parts in ONE message):**
  1. Ask one question that would naturally come up in this meeting, in English. Just the question.
  2. On a new line: `예시 답변:` then a short model answer (2–4 sentences, natural English).
  3. On a new line in Korean: "한번 직접 답해보세요."

  After TURN A, STOP. Wait for the user.

**TURN B — After the user's attempt (ONE message, always in Korean):**
  4. ONE short Korean sentence acknowledging the attempt — pick from: praise ("잘 말했어요"), specific tip ("'I am from X'보다 'I'm with X'가 더 자연스러워요"), or pronunciation/grammar note. Pick what fits best, keep it under ~15 words.
  5. On a new line: "다시 답해볼까요, 예시를 한 번 더 들어볼까요, 아니면 다음 질문으로 갈까요?"

  After TURN B, STOP. Wait for the user's choice.

**Branching by user reply after TURN B:**
- "다시 / 한 번 더 / try again" → say "좋아요, 다시 한번 답해보세요." and stop.
- "예시 / 다시 들려줘 / example again" → re-read the same `예시 답변:`, then "다시 답해보세요." and stop.
- "다음 / next / 다음 질문" → run a new TURN A with a different question. Vary topics.
- If user just gives another attempt → run TURN B on the new attempt.

**Hard rules:**
- TURN A always contains all 3 parts. Never split.
- TURN B is ALWAYS in Korean and ALWAYS ends with the 3-choices line. Never skip TURN B. Never ask a new English question in TURN B.
- You strictly alternate: TURN A → user → TURN B → user → TURN A → user → TURN B ... A new question ONLY appears when the user explicitly chose 다음/next.
- Plain prose only. No numbering, no markdown, no status notes.
""",

    "random": """

# Practice format: RANDOM (랜덤 질문 던지기)

You are running a **fast-paced random question drill**, not a structured meeting.

- Each turn: pick **one** question from the scenario's topic list at random (or generate one in that spirit), ask it in English. That's it — no preamble.
- When the user answers, give a **very short** evaluation in Korean (1–2 sentences max): one quick comment + one small improvement tip. Then immediately ask the next random question.
- Do not summarize, do not introduce yourself, do not chain related questions. Each question stands alone.
- Vary the topics. Don't repeat the same area twice in a row.
- If the user says they're done ("그만", "stop"), end gracefully with one short Korean line.
- Do NOT play the publisher persona — you are a **coach** firing practice questions.
""",

    "live": """

# Practice format: LIVE (실전 시뮬레이션)

You are running a **full live simulation** of the meeting from greeting to wrap-up. Stay 100% in character as the persona described above.

- Open with a brief, warm greeting in English and one opening question. Do not break character.
- Keep your turns short (1–3 sentences). Ask ONE question at a time.
- Aim for roughly **6–8 substantive exchanges total** before wrapping up. Adjust based on how much ground the user covers per turn — fewer turns if answers are long, more turns if they're short.
- Around the 6th–8th exchange, naturally move toward wrap-up: brief summary, 1–2 things you liked, 1–2 concerns, suggested next step. Stay in character.
- After the wrap-up, the simulation should feel naturally complete — but do NOT call end_simulation yourself. Wait for the user to indicate they're done.
- All English. If the user replies in Korean, gently steer back: "Let's keep this in English — could you say that again?"
- No meta-commentary, no out-of-character feedback. This is the closest thing to a real meeting.
""",
}


def mode_instructions(mode: str) -> str:
    """모드별 시스템 프롬프트 부록. live 만 의미 있음(guided/random 은 상태머신이 통제)."""
    return _MODE_PROMPTS.get(mode, _MODE_PROMPTS["live"]) if mode == "live" else ""


# --- guided/random 상태머신 ---
ST_ASKING = "asking"                 # 새 질문을 만들 차례
ST_WAITING_TRY = "waiting_try"       # 사용자 영어 시도 대기
ST_WAITING_CHOICE = "waiting_choice" # 사용자 선택 대기 (다시/예시/다음/그만)

# 사용자 발화에서 다음 행동을 추출. 짧은 발화일수록 선택일 가능성↑.
_AGAIN = ("다시", "한 번 더", "한번더", "다시 한번", "다시 한 번", "다시해", "다시 해",
          "try again", "again")
_EXAMPLE = ("예시", "다시 들려", "다시들려", "example", "샘플")
_NEXT = ("다음", "다음 질문", "다음으로", "next", "넥스트", "스킵", "skip", "pass")
_STOP = ("그만", "끝내", "종료", "마칠", "stop", "quit", "end")


def classify_choice(text: str) -> str | None:
    """사용자 선택 분류. None 이면 '영어 시도'로 간주.
    영어 시도가 짧을 수 있으니 키워드는 단어 경계 기반으로 본다."""
    if not text:
        return None
    t = text.lower().strip().rstrip(".!?")
    # 영어 시도는 보통 길고 선택은 짧다. 25자 넘으면 거의 시도.
    if len(t) > 30:
        return None
    # 우선순위: stop > next > example > again
    # ('예시 다시 들려줘'가 '다시'에 먼저 잡히지 않도록 example 을 again 보다 위로)
    for kw in _STOP:
        if kw in t:
            return "stop"
    for kw in _NEXT:
        if kw in t:
            return "next"
    for kw in _EXAMPLE:
        if kw in t:
            return "example"
    for kw in _AGAIN:
        if kw in t:
            return "again"
    return None


@dataclass
class Scenario:
    name: str         # 사람이 읽는 이름 (예: "Publisher First Meeting")
    key: str          # 파일 키 (확장자 제외, 예: "publisher_first_meeting")
    lang: str         # STT/TTS 언어 코드 (예: "en")
    tts_voice: str    # Supertonic voice (예: "M1")
    system_prompt: str  # LLM 에 그대로 들어가는 페르소나/규칙
    opening: str | None  # 모드 진입 시 자비스가 먼저 읽어줄 문장(영어)


def list_scenarios() -> list[str]:
    if not os.path.isdir(_SCENARIO_DIR):
        return []
    return sorted(f[:-3] for f in os.listdir(_SCENARIO_DIR) if f.endswith(".md"))


def load_scenario(key: str) -> Scenario | None:
    """scenarios/<key>.md 를 읽어 Scenario 로. 못 찾으면 None."""
    path = os.path.join(_SCENARIO_DIR, f"{key}.md")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # 첫 # 헤더를 시나리오 이름으로
    name = key
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            name = line[2:].strip()
            break
    return Scenario(
        name=name,
        key=key,
        lang=config.SIM_LANG_DEFAULT,
        tts_voice=config.SIM_TTS_VOICE,
        system_prompt=text,
        opening=config.SIM_OPENING_DEFAULT,
    )


class _Mode:
    """전역 모드 상태. 평상시(None)이거나 특정 Scenario+practice mode 활성.
    guided/random 은 추가로 상태머신(state, current_question/example, asked_topics)을 유지."""

    def __init__(self):
        self.scenario: Scenario | None = None
        self.practice: str = "live"
        # 상태머신 (guided/random 만 사용)
        self.state: str = ST_ASKING
        self.current_question: str | None = None
        self.current_example: str | None = None
        self.asked_topics: list[str] = []  # 같은 토픽 반복 방지용 누적

    def active(self) -> bool:
        return self.scenario is not None

    def start(self, key: str, practice: str = "live") -> Scenario | None:
        sc = load_scenario(key)
        if sc is None:
            return None
        self.scenario = sc
        self.practice = practice if practice in PRACTICE_MODES else "live"
        self.state = ST_ASKING
        self.current_question = None
        self.current_example = None
        self.asked_topics = []
        return sc

    def end(self) -> Scenario | None:
        sc = self.scenario
        self.scenario = None
        self.practice = "live"
        self.state = ST_ASKING
        self.current_question = None
        self.current_example = None
        self.asked_topics = []
        return sc

    # --- 다른 모듈이 매 호출마다 참조 ---
    def stt_lang(self) -> str:
        # guided/random 은 사용자가 짧은 한국어로 다음 행동을 말할 수 있어야 함
        # → 자동 감지(None) 가 안전. live 만 영어 고정.
        if not self.scenario:
            return config.WHISPER_LANG
        return self.scenario.lang if self.practice == "live" else None

    def tts_lang(self) -> str:
        # guided/random 은 한국어 피드백이 섞이므로 한국어로 두는 게 자연스럽다
        # (Supertonic 은 영어 단어도 한국어 음성으로 읽어줌 — 자연스러움)
        if not self.scenario:
            return config.SUPERTONIC_LANG
        return self.scenario.lang if self.practice == "live" else "ko"

    def tts_voice(self) -> str:
        if not self.scenario:
            return config.SUPERTONIC_VOICE
        # live: 페르소나 음성(영어). guided/random: 평상시 음성(코치 톤).
        return self.scenario.tts_voice if self.practice == "live" else config.SUPERTONIC_VOICE

    def system_prompt(self) -> str | None:
        """시나리오 페르소나 + 모드별 진행 지침. 평상시 None."""
        if not self.scenario:
            return None
        return self.scenario.system_prompt + mode_instructions(self.practice)


MODE = _Mode()
