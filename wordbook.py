"""워드북 — 고유명사/전문용어를 STT 와 LLM 양쪽에 주입.

기본 파일은 평상시 자비스용 (wordbook.txt). 회의 모드 등 다른 컨텍스트에선
경로를 인자로 넘겨 별도 파일을 사용할 수 있다 (예: wordbook_meet.txt).

형식 (한 줄에 하나):
  Jarvis                    → 정식 표기만
  NVIDIA=엔비디아,너비디아   → 정식=오인식 표현들(쉼표 구분)

쓰임:
  - STT: load_initial_prompt(path) → faster-whisper 의 initial_prompt 로 전달.
  - STT 후처리: apply_aliases(text, path) → 오인식 표기를 정식 표기로 자동 치환.
  - LLM: load_system_hint(path) → 시스템 프롬프트에 어휘 힌트 덧붙임.
"""
import os
import re
from functools import lru_cache

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordbook.txt")
MEET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordbook_meet.txt")


@lru_cache(maxsize=8)
def _parse(path: str = _DEFAULT_PATH) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """(terms, aliases) 튜플로 반환 (lru_cache 위해 hashable). path 별로 따로 캐시."""
    terms: list[str] = []
    aliases: dict[str, str] = {}
    if not os.path.exists(path):
        return tuple(), tuple()
    with open(path, encoding="utf-8") as f:
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
    return tuple(terms), tuple(aliases.items())


def _data(path: str | None) -> tuple[list[str], dict[str, str]]:
    """캐시된 튜플을 호출자가 쓰기 좋은 형태로."""
    terms, alias_pairs = _parse(path or _DEFAULT_PATH)
    return list(terms), dict(alias_pairs)


def load_initial_prompt(max_chars: int = 240, path: str | None = None) -> str | None:
    """Whisper initial_prompt 용 어휘 문장. 너무 길면 환각이 늘어 제한.
    한국어 음역 별칭도 함께 넣어 발음 단서를 준다."""
    terms, aliases = _data(path)
    if not terms and not aliases:
        return None
    seen, words = set(), []
    for w in terms + list(aliases.keys()):
        if w not in seen:
            seen.add(w); words.append(w)
    s = ", ".join(words)
    if len(s) > max_chars:
        s = s[:max_chars].rsplit(",", 1)[0]
    return f"다음 어휘가 등장할 수 있다: {s}."


def apply_aliases(text: str, path: str | None = None) -> str:
    """오인식 별칭을 정식 표기로 치환.
    별칭 안의 공백은 "선택적 문장부호 + 공백" 으로 유연하게 매칭한다."""
    if not text:
        return text
    _, aliases = _data(path)
    if not aliases:
        return text
    # 긴 별칭부터 우선 매칭(부분일치 충돌 방지)
    for alt in sorted(aliases, key=len, reverse=True):
        canon = aliases[alt]
        tokens = re.split(r"\s+", alt.strip())
        if not tokens:
            continue
        pattern = r"[,\.\?!\s]*".join(re.escape(tok) for tok in tokens)
        text = re.sub(pattern, canon, text, flags=re.IGNORECASE)
    return text


def load_glossary_lines(path: str | None = None) -> list[str]:
    """LLM 시스템 프롬프트용 사전 라인 목록.
    예: 'Concode (variants: 콩코드, 컨코드, 콘코드, 콩코도)'
    번역기에 '이런 발음으로 들어오면 이 정식 표기로 옮겨라' 신호를 명확히 준다."""
    terms, aliases = _data(path)
    # canonical → 변형 목록 역색인
    variants: dict[str, list[str]] = {t: [] for t in terms}
    for alt, canon in aliases.items():
        variants.setdefault(canon, []).append(alt)
    lines = []
    for canon in terms:
        alts = variants.get(canon, [])
        if alts:
            lines.append(f"{canon} (variants: {', '.join(alts)})")
        else:
            lines.append(canon)
    return lines


def load_system_hint(max_terms: int = 40, path: str | None = None) -> str:
    """LLM 시스템 프롬프트에 덧붙일 어휘 힌트. 비어 있으면 ''."""
    terms, aliases = _data(path)
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
