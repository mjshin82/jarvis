"""회의 언어 코드 정규화/매핑. 입력(웹 jp, 콘솔 'ko,en,ja') → 정규 코드."""

ALIAS = {"jp": "ja"}                       # 사용자 표기 → 표준
NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}
GLADIA = {"ko": "ko", "en": "en", "ja": "ja", "zh": "zh"}
DEFAULT = ["ko", "en"]


def normalize(codes) -> list:
    """리스트 또는 쉼표문자열 → 정규 코드(jp→ja), 유효(NAMES)만, 순서보존 중복제거.
    비거나 전부 무효면 DEFAULT 복사."""
    if isinstance(codes, str):
        codes = codes.split(",")
    out = []
    for c in (codes or []):
        c = (c or "").strip().lower()
        c = ALIAS.get(c, c)
        if c in NAMES and c not in out:
            out.append(c)
    return out or list(DEFAULT)


def names(codes) -> list:
    """정규 코드 → 영어 언어명 리스트 (LLM 프롬프트용)."""
    return [NAMES[c] for c in normalize(codes)]


def gladia_codes(codes) -> list:
    """정규 코드 → Gladia language_config 코드."""
    return [GLADIA[c] for c in normalize(codes)]
