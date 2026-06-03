"""연습 모드의 LLM 마이크로 작업.

질문/예시 답변은 코드(qa.py 의 QA 뱅크)가 직접 고르고, LLM 은 두 가지만 한다:
  evaluate(question, expected, attempt) → 한국어 한 줄 코칭 코멘트
  live_phrase(persona_prompt, question)  → 페르소나 톤으로 질문 wording 변형
                                            (live 모드 전용; 실패 시 원문 그대로 사용)

LLM 호출은 비스트리밍, 짧은 max_tokens → 4B 모델로도 안정적이고 빠르다.
"""
from openai import AsyncOpenAI


_EVAL_SYS = (
    "You are an English speaking coach for a Korean learner practicing for a meeting.\n"
    "You have the question being practiced and the expected/ideal answer the learner is\n"
    "working toward, plus the learner's actual attempt.\n"
    "\n"
    "Give ONE short English sentence (max ~18 words) of coaching feedback. Pick whatever\n"
    "is most useful for this attempt:\n"
    " - praise a specific phrase or content that matched well, OR\n"
    " - point out a key piece from the expected answer the learner missed, OR\n"
    " - suggest a more natural English phrasing, OR\n"
    " - note a grammar or pronunciation issue, OR\n"
    " - if the learner spoke Korean, gently remind them to try in English.\n"
    "\n"
    "Speak as a friendly coach (\"you said...\", \"try saying...\", \"nice — that's clear\").\n"
    "Output ONLY that one English sentence. No quotes, no extra text, no preamble."
)

_LIVE_REACT_SYS = (
    "You are a publisher BD in a real first meeting. The user just answered a question.\n"
    "Respond IN CHARACTER with ONE short, warm English line (max ~12 words) that acknowledges\n"
    "their answer naturally — e.g. 'Got it, thanks.' / 'That's helpful, thanks.' / 'Interesting.'\n"
    "Do NOT ask a follow-up question. Do NOT give meta feedback. Just a brief, natural reaction.\n"
    "Output ONLY that one English line."
)


_LIVE_SYS = (
    "Rewrite the given meeting question in natural spoken English, in the voice of\n"
    "the persona described below. Keep the same meaning. Output ONE sentence only.\n"
    "It should sound warm and conversational, like a publisher BD asking in a real\n"
    "first meeting. No preamble, no quotes — just the rewritten question."
)


async def evaluate(client: AsyncOpenAI, model: str,
                   question: str, expected: str, attempt: str,
                   extra: dict) -> str:
    """사용자 시도에 대한 한국어 한 줄 코멘트."""
    if not attempt.strip():
        return "I didn't catch that — try again, please."
    user_msg = (
        f"Question (English): {question}\n"
        f"Expected/ideal answer: {expected}\n"
        f"Learner's attempt: {attempt}\n\n"
        "Give one short English coaching sentence now."
    )
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EVAL_SYS},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=80,
            temperature=0.4,
            extra_body=extra,
        )
        text = (r.choices[0].message.content or "").strip()
        text = text.strip("'\"`").strip()
        return text or "Nice — keep practicing."
    except Exception as ex:
        print(f"[coach] evaluate fallback: {ex}")
        return "Nice — keep practicing."


async def live_react(client: AsyncOpenAI, model: str, attempt: str, extra: dict) -> str:
    """live 모드: 사용자 답에 짧게 영어로 반응 (캐릭터 유지). 다음 질문은 별도로."""
    if not attempt.strip():
        return "Sorry, I didn't catch that."
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LIVE_REACT_SYS},
                {"role": "user", "content": f"Learner said: {attempt}\nReact in one line."},
            ],
            max_tokens=40,
            temperature=0.5,
            extra_body=extra,
        )
        text = (r.choices[0].message.content or "").strip()
        text = text.strip("'\"`").strip()
        text = text.splitlines()[0].strip() if text else ""
        return text or "Got it, thanks."
    except Exception as ex:
        print(f"[coach] live_react fallback: {ex}")
        return "Got it, thanks."


async def live_phrase(client: AsyncOpenAI, model: str,
                      persona_prompt: str, question: str,
                      extra: dict) -> str:
    """live 모드: 데이터의 영어 질문을 페르소나 톤으로 wording 변형.
    실패하거나 응답이 이상하면 원문을 그대로 돌려준다."""
    persona_snippet = persona_prompt[:1500]   # 너무 길지 않게
    user_msg = (
        f"Persona:\n---\n{persona_snippet}\n---\n\n"
        f"Original question: {question}\n\n"
        "Rewrite as one natural spoken English sentence."
    )
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LIVE_SYS},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=120,
            temperature=0.7,
            extra_body=extra,
        )
        text = (r.choices[0].message.content or "").strip()
        text = text.strip("'\"`").strip()
        # 한 문장만 — 줄바꿈 들어오면 첫 줄만
        text = text.splitlines()[0].strip() if text else ""
        return text or question
    except Exception as ex:
        print(f"[coach] live_phrase fallback: {ex}")
        return question


_TO_KO_SYS = (
    "You are a translator. Translate the user's text into natural, conversational Korean.\n"
    "Rules:\n"
    " - Output ONLY the Korean translation. No explanations, no quotes, no source text.\n"
    " - If the text is already in Korean, output it as-is.\n"
    " - Keep the tone informal but polite (해요체).\n"
    " - Preserve proper nouns; transliterate only when natural."
)

_TO_EN_SYS = (
    "You are a translator. Translate the user's text into natural, conversational English.\n"
    "Rules:\n"
    " - Output ONLY the English translation. No explanations, no quotes, no source text.\n"
    " - If the text is already in English, output it as-is.\n"
    " - Use a natural, professional but warm tone — like in a real business meeting.\n"
    " - Preserve proper nouns as-is."
)


async def _translate(client, model, text: str, system: str, extra: dict) -> str:
    text = text.strip()
    if not text:
        return ""
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            max_tokens=400,
            temperature=0.2,
            extra_body=extra,
        )
        out = (r.choices[0].message.content or "").strip()
        return out.strip("'\"`").strip() or text
    except Exception as ex:
        print(f"[coach] translate fallback: {ex}")
        return text


async def translate_to_korean(client, model, text: str, extra: dict) -> str:
    """발화를 한국어로 옮긴다. 실패하면 원문 그대로."""
    return await _translate(client, model, text, _TO_KO_SYS, extra)


async def translate_to_english(client, model, text: str, extra: dict) -> str:
    """발화를 영어로 옮긴다. 실패하면 원문 그대로."""
    return await _translate(client, model, text, _TO_EN_SYS, extra)


def is_korean(text: str) -> bool:
    """한글 음절이 포함되어 있으면 한국어 발화로 본다."""
    if not text:
        return False
    return any(0xAC00 <= ord(c) <= 0xD7A3 for c in text)


# --- 회의 전용 번역 (DeepSeek 등 고품질 모델용) ---
# 시스템 프롬프트는 매 호출마다 *정확히 동일* 하게 보내야 DeepSeek 의 자동
# prompt caching 이 히트한다(같은 prefix 재사용 → 입력 토큰 1/10 가격).
# 그래서 워드북/회의 맥락을 모두 한 번에 system 으로 묶는다.

_MEET_SYS_TEMPLATE = """You are a professional simultaneous interpreter for a business meeting.

Translate each input utterance into the target language. Output ONLY the translation — no labels, no quotes, no source text, no commentary.

Direction (auto):
- If the input contains any Korean characters → translate to natural, professional English.
- Otherwise (English/Japanese/etc.) → translate to natural Korean (해요체).

Quality rules:
- Conversational and natural — what a fluent bilingual interpreter would actually say in a real meeting, NOT a literal word-for-word translation.
- Preserve all proper nouns exactly as listed below. STT often mishears these — if the input has a near-miss, recognize and correct using this glossary.
- Numbers, percentages, dates, units: keep them numeric when natural; spell out only when it reads better.
- If the source sentence is fragmented or short, still produce a clean, fluent target sentence.
- Never add information that isn't in the source. Never refuse — if you can't translate, output the source as-is.

Meeting context:
{context}

Proper nouns glossary (recognize variants in input, output the canonical form):
{glossary}

Examples:
입력: 만나서 반갑습니다. 저는 콩코드의 신명진입니다.
출력: Nice to meet you. I'm Myungjin Shin from Concode.

Input: Could you tell us about Graytail in one sentence?
출력: 그레이테일을 한 문장으로 소개해 주시겠어요?

Input: We're a two-person studio.
출력: 저희는 2인 스튜디오예요."""


def _build_meet_system_prompt(context: str, glossary_lines: list[str]) -> str:
    """회의용 시스템 프롬프트. 캐시 히트를 위해 입력만 바뀌고 시스템은 고정."""
    glossary = "\n".join(f"- {line}" for line in glossary_lines) if glossary_lines else "- (none)"
    ctx = context.strip() if context else "(general business meeting)"
    return _MEET_SYS_TEMPLATE.format(context=ctx, glossary=glossary)


async def translate_meeting(client, model: str, text: str,
                            system_prompt: str, extra: dict | None = None) -> str:
    """회의 전용 양방향 번역. 시스템 프롬프트는 호출자가 만들어 매번 동일하게 보냄
    (DeepSeek prompt caching 활용). 실패하면 원문 그대로."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=500,
            temperature=0.2,
            extra_body=extra or {},
        )
        out = (r.choices[0].message.content or "").strip()
        return out.strip("'\"`").strip() or text
    except Exception as ex:
        print(f"[coach] translate_meeting fallback: {ex}")
        return text


# --- 고정 멘트 ---
CHOICES_PROMPT = "다시 답해볼까요, 예시를 한 번 더 들어볼까요, 아니면 다음 질문으로 갈까요?"
TRY_AGAIN_PROMPT = "좋아요, 다시 한번 답해보세요."
EXAMPLE_PREFIX = "예시 답변:"
TRY_PROMPT_KO = "한번 직접 답해보세요."
STOP_PROMPT_KO = "이번 연습은 여기까지 할게요."
ALL_DONE_PROMPT = "준비된 질문을 모두 다뤘어요. 멋졌어요. 연습을 마칠게요."
