"""Guided/Random 모드의 LLM 마이크로 작업.

코드 상태머신이 흐름을 통제하고, LLM 은 작고 명확한 두 가지 작업만 한다:
  make_question(scenario_prompt, asked_topics) → {question, example, topic}
  evaluate(question, attempt) → 한국어 한 줄 코멘트

LLM 호출은 비스트리밍, max_tokens 제한, JSON 강제 → 4B 모델로도 안정적.
"""
import json
from openai import AsyncOpenAI

import config


_QUESTION_SYS = (
    "You generate ONE practice question (in English) and a short model answer "
    "for an English meeting practice app. The scenario prompt below describes the "
    "meeting context and what topics naturally come up.\n\n"
    "Output STRICT JSON with these keys:\n"
    '  "topic"    : short English label of the topic (e.g. "team intro", "elevator pitch")\n'
    '  "question" : the question to ask the user, in natural spoken English (one sentence)\n'
    '  "example"  : a model answer the user could give (2–4 short sentences, natural spoken English)\n\n'
    "Avoid topics in the 'already_asked' list — pick something different. "
    "Keep both question and example short enough to read aloud naturally. "
    "Output JSON only, no markdown, no commentary."
)

_EVAL_SYS = (
    "You are an English speaking coach for a Korean learner. The learner just attempted "
    "to answer a meeting question in English. Give ONE short Korean sentence (max ~25 chars) "
    "that helps them improve. Pick the most useful angle:\n"
    " - praise a specific phrase that worked, OR\n"
    " - suggest a better phrasing for something awkward, OR\n"
    " - note a grammar/word issue, OR\n"
    " - if they spoke Korean, gently say to try in English.\n"
    "Output ONLY the Korean sentence. No quotes, no extra text."
)


async def make_question(client: AsyncOpenAI, model: str, scenario_prompt: str,
                        asked_topics: list[str], extra: dict) -> dict:
    """새 질문 + 예시 답변 생성. {'topic','question','example'} 반환.
    실패 시 합리적 폴백."""
    user_msg = (
        f"Scenario prompt:\n---\n{scenario_prompt[:3500]}\n---\n\n"
        f"already_asked: {asked_topics or '(none)'}\n\n"
        "Return JSON now."
    )
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _QUESTION_SYS},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_tokens=400,
            temperature=0.8,
            extra_body=extra,
        )
        content = r.choices[0].message.content or "{}"
        data = json.loads(content)
        # 필드 가드
        q = (data.get("question") or "").strip()
        e = (data.get("example") or "").strip()
        t = (data.get("topic") or "").strip() or "general"
        if not q or not e:
            raise ValueError("missing field")
        return {"topic": t, "question": q, "example": e}
    except Exception as ex:
        print(f"[coach] make_question fallback: {ex}")
        return {
            "topic": "general",
            "question": "Could you tell me a bit about your team and your current project?",
            "example": ("Sure — we're a two-person studio called Concode. I handle programming "
                        "and business, my partner leads art. We're working on a narrative game "
                        "called Graytail."),
        }


async def evaluate(client: AsyncOpenAI, model: str, question: str, attempt: str,
                   extra: dict) -> str:
    """사용자 시도에 대한 한국어 한 줄 코멘트."""
    if not attempt.strip():
        return "음성이 잡히지 않았어요. 다시 한번 답해보세요."
    user_msg = (
        f"Question (English): {question}\n"
        f"Learner's attempt: {attempt}\n\n"
        "Give one short Korean sentence of coaching feedback now."
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
        # 따옴표/마크다운 흔적 제거
        text = text.strip("'\"`").strip()
        return text or "좋아요, 계속 연습해봐요."
    except Exception as ex:
        print(f"[coach] evaluate fallback: {ex}")
        return "좋아요, 계속 연습해봐요."


CHOICES_PROMPT = "다시 답해볼까요, 예시를 한 번 더 들어볼까요, 아니면 다음 질문으로 갈까요?"
TRY_AGAIN_PROMPT = "좋아요, 다시 한번 답해보세요."
EXAMPLE_PREFIX = "예시 답변:"
TRY_PROMPT_KO = "한번 직접 답해보세요."
STOP_PROMPT_KO = "이번 연습은 여기까지 할게요."
