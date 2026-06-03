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
import random
from dataclasses import dataclass

import config

# Supertonic 의 사용 가능한 보이스 풀(M1~M5 / F1~F5). 시뮬 진입 시 평상시 음성을 제외하고
# 이 풀에서 무작위 선택. 진입할 때마다 다른 음성으로 들리게 하는 게 목적.
_ALL_VOICES = tuple(f"{g}{i}" for g in ("M", "F") for i in range(1, 6))


def random_sim_voice(exclude: str | None = None) -> str:
    """평상시 음성을 제외하고 무작위로 하나 선택. 다음 후보가 없으면 평상시 그대로."""
    pool = [v for v in _ALL_VOICES if v != exclude]
    return random.choice(pool) if pool else (exclude or "F1")

_SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")

PRACTICE_MODES = ("guided", "random", "live")

# 모든 모드의 흐름을 코드 상태머신이 통제한다. LLM 은 마이크로 작업(평가, live 의
# wording 변형)만 한다. 따라서 시스템 프롬프트 부록은 더 이상 필요 없다.
# 호환을 위해 정의는 남기되 비워둔다.
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
    """모드별 시스템 프롬프트 부록. 모든 모드를 상태머신이 통제하므로 빈 문자열."""
    return ""


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


# 모드 선택용 키워드 (모드 미지정 진입 시 사용자 다음 발화 분류).
# Whisper 가 짧은 한국어 명령을 자주 헛들으니 음역 변형까지 별칭으로 포함.
_MODE_KEYWORDS = {
    "guided": (
        "가이드", "guided", "guide", "예시", "답변 예시", "코칭", "코치", "코스",
        "1번", "첫번째", "일번",
        # 흔한 오인식: '가이드'의 첫 음 탈락/변형
        "아이드", "아이딩", "아이들", "가이딩", "가이트", "카이드", "하이드",
    ),
    "random": (
        "랜덤", "random", "랜덤하게", "무작위", "막", "2번", "두번째", "이번",
        # 흔한 오인식
        "랜듬", "렌덤", "랜점", "랜담",
    ),
    "live":   (
        "실전", "live", "라이브", "그냥", "진짜", "실제", "롤플레이", "미팅처럼",
        "3번", "세번째", "삼번",
        # 흔한 오인식
        "시전", "실선", "신전", "실전모드", "라이프",
    ),
}


def classify_mode(text: str) -> str | None:
    """사용자 발화에서 연습 모드를 분류. 못 잡으면 None."""
    if not text:
        return None
    t = text.lower().strip()
    # 가장 긴 키워드부터 보면 부분일치 충돌 줄어듦
    for mode, kws in _MODE_KEYWORDS.items():
        for kw in sorted(kws, key=len, reverse=True):
            if kw in t:
                return mode
    return None


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
    guided/random 은 추가로 상태머신(state, current_question/example, asked_topics)을 유지.
    pending_scenario: 사용자가 시뮬 진입을 요청했지만 아직 모드를 안 골랐을 때 임시 보관."""

    def __init__(self):
        self.scenario: Scenario | None = None
        self.practice: str = "live"
        self.pending_scenario: str | None = None   # 모드 선택 대기 중인 시나리오 키
        # 상태머신 (guided/random/live 공통: QA 뱅크에서 골라 진행)
        self.state: str = ST_ASKING
        self.current_question: str | None = None
        self.current_example: str | None = None
        # QA 뱅크 진행 누적 (qa.key_of 가 만든 'sectionIdx:qIdx' 문자열들)
        self.asked_keys: list[str] = []
        # 시뮬 진입 시 평상시 음성 제외한 풀에서 무작위 선택, 진입 동안 유지
        self.sim_voice: str | None = None

    def is_pending_mode(self) -> bool:
        return self.pending_scenario is not None and self.scenario is None

    def active(self) -> bool:
        return self.scenario is not None

    def start(self, key: str, practice: str = "live") -> Scenario | None:
        sc = load_scenario(key)
        if sc is None:
            return None
        self.scenario = sc
        self.practice = practice if practice in PRACTICE_MODES else "live"
        self.pending_scenario = None
        self.state = ST_ASKING
        self.current_question = None
        self.current_example = None
        self.asked_keys = []
        # 진입할 때마다 메인 음성을 뺀 풀에서 새로 추첨 → 매번 다른 화자 느낌
        self.sim_voice = random_sim_voice(exclude=config.SUPERTONIC_VOICE)
        return sc

    def end(self) -> Scenario | None:
        sc = self.scenario
        self.scenario = None
        self.practice = "live"
        self.pending_scenario = None
        self.state = ST_ASKING
        self.current_question = None
        self.current_example = None
        self.asked_keys = []
        self.sim_voice = None
        return sc

    def set_pending(self, key: str):
        """모드 선택 대기 상태. 시나리오는 아직 활성화 안 함."""
        self.scenario = None
        self.pending_scenario = key

    def clear_pending(self):
        self.pending_scenario = None

    # --- 다른 모듈이 매 호출마다 참조 ---
    def stt_initial_prompt(self) -> str | None:
        """현재 상태에 특화된 STT 컨디셔닝. 좁고 집중된 힌트가 일반 워드북보다 강하게 작용.
        None 을 돌려주면 stt.py 가 기본 워드북 프롬프트를 쓴다."""
        # 모드 선택 대기 중: 셋 중 하나가 들어올 가능성이 압도적으로 높음
        if self.is_pending_mode():
            return "다음 중 하나를 한국어로 말한다: 가이드, 랜덤, 실전."
        # guided 의 선택 대기: 4개 명령 중 하나
        if self.scenario and self.practice == "guided" and self.state == ST_WAITING_CHOICE:
            return "다음 중 하나를 한국어로 말한다: 다시, 예시, 다음, 그만."
        return None

    def stt_lang(self) -> str:
        """현재 상태에 맞는 STT 언어. 잘못 잡으면 영어 발화가 한국어로 음역되는 일이 생기므로
        무조건 시나리오 언어로 고정하되, '선택 대기' 상태(다시/예시/다음 같은 한국어 명령
        대기)일 때만 한국어로 고정."""
        if not self.scenario:
            return config.WHISPER_LANG
        # guided 에서 사용자가 선택을 말할 때는 한국어 고정
        if self.practice == "guided" and self.state == ST_WAITING_CHOICE:
            return "ko"
        # 그 외(영어 시도, live 의 모든 응답)는 시나리오 언어(영어)
        return self.scenario.lang

    def tts_lang(self) -> str:
        # 시뮬 모드는 어느 모드든 시나리오 언어(영어). guided/random 도 피드백을 영어로 주므로
        # 일관된 영어 음성으로 가는 게 자연스럽다. 안내 멘트(한국어)는 Supertonic 이
        # 영어 음성으로도 한국어를 그대로 발화 가능.
        if not self.scenario:
            return config.SUPERTONIC_LANG
        return self.scenario.lang

    def tts_voice(self) -> str:
        # 시뮬 모드에선 진입 시 추첨된 음성(평상시 음성 제외)을 진입 동안 유지.
        # 종료하면 평상시 음성으로 복귀.
        if not self.scenario:
            return config.SUPERTONIC_VOICE
        return self.sim_voice or self.scenario.tts_voice

    def system_prompt(self) -> str | None:
        """시나리오 페르소나 + 모드별 진행 지침. 평상시 None."""
        if not self.scenario:
            return None
        return self.scenario.system_prompt + mode_instructions(self.practice)


MODE = _Mode()
