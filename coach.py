"""번역/회의 모드용 LLM 헬퍼.

- translate_to_korean / translate_to_english : /trans 모드의 가벼운 단방향 번역
- translate_meeting + _build_meet_system_prompt : /meet 의 양방향 번역.
  시스템 프롬프트를 한 번만 빌드해서 매 호출 동일하게 보내면 DeepSeek 의
  자동 prompt caching 이 히트해 입력 토큰 ~1/10 가격으로 떨어진다.
- is_korean(text) : 한글 음절 감지 (번역 방향 자동 분기에 사용)
"""


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


# --- 다국어 회의 전용 번역 (단일 호출 → 모든 룸 언어로 동시 번역) ---

_MEET_MULTI_TEMPLATE = """You are a professional simultaneous interpreter for a business meeting.
Room languages: {langs}.
Detect the language of each input utterance and translate it into ALL the OTHER room languages (exclude the source language).
Output ONLY a JSON object whose keys are EXACTLY from this set, minus the source language's code: {codes}. Do NOT output any language outside this set. No commentary, no source-language key, no code fences.

Quality rules:
- Natural and conversational — what a fluent interpreter would actually say, not word-for-word.
- Preserve proper nouns exactly (see glossary); correct STT near-misses.
- Never add information not in the source.

Meeting context:
{context}

Proper nouns glossary (recognize variants, output canonical form):
{glossary}"""


def build_multi_system_prompt(lang_names: list, lang_codes: list, context: str, glossary_lines: list) -> str:
    """다국어 회의 번역 시스템 프롬프트. 룸 언어 고정 → 회의당 동일(캐시 히트).
    lang_codes: 출력 허용 코드(룸 언어). 이 밖의 언어는 내지 말라고 명시."""
    glossary = "\n".join(f"- {l}" for l in glossary_lines) if glossary_lines else "- (none)"
    ctx = (context or "").strip() or "(general business meeting)"
    return _MEET_MULTI_TEMPLATE.format(
        langs=", ".join(lang_names), codes=", ".join(lang_codes), context=ctx, glossary=glossary)


_MEET_BILINGUAL_TEMPLATE = """You are a professional simultaneous interpreter for a business meeting between two languages: {a_name} ({a_code}) and {b_name} ({b_code}).
For each input utterance, detect which of these two languages it is in and translate it into the OTHER one.
- Input in {a_name} → translate to {b_name}; output {{"{b_code}": "..."}}.
- Input in {b_name} → translate to {a_name}; output {{"{a_code}": "..."}}.
Output ONLY that JSON object with exactly one key (the target code). Never output the source language, no other languages, no commentary, no code fences.

Quality rules:
- Natural and conversational — what a fluent interpreter would actually say, not word-for-word.
- Preserve proper nouns exactly (see glossary); correct STT near-misses.
- Never add information not in the source.

Meeting context:
{context}

Proper nouns glossary (recognize variants, output canonical form):
{glossary}"""


def build_bilingual_system_prompt(lang_names: list, lang_codes: list, context: str, glossary_lines: list) -> str:
    """언어 2개 회의용 — 상대 언어를 명시한 양방향 지향 프롬프트(오번역에 강함).
    lang_names/lang_codes 는 길이 2 (룸 언어)."""
    glossary = "\n".join(f"- {l}" for l in glossary_lines) if glossary_lines else "- (none)"
    ctx = (context or "").strip() or "(general business meeting)"
    return _MEET_BILINGUAL_TEMPLATE.format(
        a_name=lang_names[0], a_code=lang_codes[0],
        b_name=lang_names[1], b_code=lang_codes[1],
        context=ctx, glossary=glossary)


def _parse_json_obj(s: str) -> dict:
    """LLM 출력에서 JSON 오브젝트만 추출(코드펜스/잡텍스트 방어). 실패 시 {}."""
    import json
    import re
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "{" in s:
            s = s[s.find("{"):]
    try:
        d = json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return {}
        try:
            d = json.loads(m.group(0))
        except Exception:
            return {}
    if not isinstance(d, dict):
        return {}
    return {str(k): str(v) for k, v in d.items() if isinstance(v, str) and v.strip()}


async def translate_multi(client, model: str, text: str,
                          system_prompt: str, extra: dict | None = None) -> dict:
    """발화 1건을 룸의 나머지 언어들로 번역(단일 호출, JSON). 실패/빈 입력 → {}."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=800,
            temperature=0.2,
            extra_body=extra or {},
        )
        out = (r.choices[0].message.content or "").strip()
        return _parse_json_obj(out)
    except Exception as ex:
        print(f"[coach] translate_multi fallback: {ex}")
        return {}
