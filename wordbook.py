"""워드북 — 고유명사/전문용어를 STT 와 LLM 양쪽에 주입.

형식(wordbook.txt 한 줄에 하나):
  Jarvis                    → 정식 표기만
  NVIDIA=엔비디아,너비디아   → 정식=오인식 표현들(쉼표 구분)

쓰임:
  - STT: load_initial_prompt() → faster-whisper 의 initial_prompt 로 전달.
         "이 어휘가 나올 거다" 컨디셔닝. 음향적으로 비슷한 단어 가중치↑.
  - STT 후처리: apply_aliases(text) → 오인식 표기를 정식 표기로 자동 치환.
  - LLM: load_system_hint() → 시스템 프롬프트에 어휘 목록을 덧붙여,
         STT 가 살짝 빗나가도 LLM 이 문맥상 알아챌 수 있게 한다.
"""
import os
import re
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordbook.txt")


@lru_cache(maxsize=1)
def _parse() -> tuple[list[str], dict[str, str]]:
    """(canonical_terms, alias→canonical) 를 돌려준다. 파일이 없으면 빈 결과."""
    terms: list[str] = []
    aliases: dict[str, str] = {}
    if not os.path.exists(_PATH):
        return terms, aliases
    with open(_PATH, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                canon, rest = line.split("=", 1)
                canon = canon.strip()
                if not canon:
                    continue
                terms.append(canon)
                for alt in rest.split(","):
                    alt = alt.strip()
                    if alt and alt.lower() != canon.lower():
                        aliases[alt] = canon
            else:
                terms.append(line)
    return terms, aliases


def load_initial_prompt(max_chars: int = 240) -> str | None:
    """Whisper initial_prompt 용 어휘 문장. 너무 길면 환각이 늘어 제한.
    한국어 음역 별칭도 함께 넣어 발음 단서를 준다."""
    terms, aliases = _parse()
    if not terms and not aliases:
        return None
    # 정식 + 별칭(중복 제거, 순서 보존)
    seen, words = set(), []
    for w in terms + list(aliases.keys()):
        if w not in seen:
            seen.add(w); words.append(w)
    s = ", ".join(words)
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(",", 1)[0]
    return f"다음 어휘가 등장할 수 있다: {s}."


def apply_aliases(text: str) -> str:
    """오인식 별칭을 정식 표기로 치환.
    별칭 안의 공백은 "선택적 문장부호 + 공백" 으로 유연하게 매칭한다 — Whisper 가 짧은
    명령 사이에 쉼표/마침표를 끼워넣는 경향(예: '마크, 고도.')을 흡수하기 위함."""
    if not text:
        return text
    _, aliases = _parse()
    if not aliases:
        return text
    # 긴 별칭부터 우선 매칭(부분일치 충돌 방지)
    for alt in sorted(aliases, key=len, reverse=True):
        canon = aliases[alt]
        # 별칭을 토큰으로 쪼개서 사이에 문장부호+공백 변형을 허용하는 정규식 생성
        tokens = re.split(r"\s+", alt.strip())
        if not tokens:
            continue
        pattern = r"[,\.\?!\s]*".join(re.escape(tok) for tok in tokens)
        text = re.sub(pattern, canon, text, flags=re.IGNORECASE)
    return text


def load_system_hint(max_terms: int = 40) -> str:
    """LLM 시스템 프롬프트에 덧붙일 어휘 힌트. 비어 있으면 ''."""
    terms, aliases = _parse()
    if not terms and not aliases:
        return ""
    seen, words = set(), []
    for w in terms + list(aliases.values()):
        if w not in seen:
            seen.add(w); words.append(w)
    words = words[:max_terms]
    return (
        " 사용자가 말하는 다음 고유명사/용어를 알아들어라"
        " (음성 인식이 비슷한 발음으로 잘못 들어와도 문맥에서 추론): "
        + ", ".join(words) + "."
    )
