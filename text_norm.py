"""TTS 직전 텍스트 정규화 — 숫자/단위/기호를 발화 가능한 형태로 변환.

Supertonic 같은 TTS 모델은 한국어 G2P 가 약해 '2025년' 을 잘못 발음하는 경우가
많다. 여기서 숫자/단위를 미리 풀어 써서 그 부담을 덜어준다.

핵심:
- 단위가 따라붙은 숫자는 단위와 함께 변환 ('30%' → '삼십 퍼센트')
- 한국어 카운터(개/명/번/장/마리/대/잔/병 등)는 1~99 까지 고유어 사용
  ('3개' → '세 개', '21번' → '스물한 번')
- 그 외 큰 숫자/년도는 한자어 ('2025' → '이천이십오')
- 소수, 시간(HH:MM), 통화($/원/엔)도 처리
- 언어는 lang 인자로 받음. 기본 'ko'. 'ja' 면 일본어, 'en' 이면 영어 단어로.

언어별 라이브러리:
- num2words 가 'ko'/'ja'/'en' 모두 지원
- 한국어 카운터 고유어는 직접 매핑(num2words 가 일관되게 지원하지 않음)
"""
import re

from num2words import num2words

# --- 한국어 카운터 (1~99 고유어) ---
# 카운터 뒤에 오면 앞 숫자를 고유어로 읽는 것이 자연스러움.
_KO_COUNTERS = (
    "개", "명", "분", "번", "장", "마리", "대", "잔", "병", "권", "벌",
    "켤레", "송이", "그루", "통", "다발", "줄", "살", "시간", "달", "해",
    "살", "살짜리", "분짜리",
)

# 1~10 고유어 (관형사형)
_KO_NATIVE_UNITS = {
    1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯",
    6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉", 10: "열",
}
# 10단위 고유어
_KO_NATIVE_TENS = {
    20: "스무", 30: "서른", 40: "마흔", 50: "쉰",
    60: "예순", 70: "일흔", 80: "여든", 90: "아흔",
}


def _ko_native_number(n: int) -> str:
    """1~99 까지의 한국어 고유어 관형사형. (3 → '세', 21 → '스물한')"""
    if n < 1 or n > 99:
        return num2words(n, lang="ko")
    if n in _KO_NATIVE_UNITS:
        return _KO_NATIVE_UNITS[n]
    tens, ones = divmod(n, 10)
    base = tens * 10
    if ones == 0:
        # 20/30/40/... 단독
        if base == 20:
            return "스무"
        return _KO_NATIVE_TENS.get(base, num2words(n, lang="ko"))
    # 21 → 스물 + 한, 35 → 서른 + 다섯
    tens_word = {2: "스물", 3: "서른", 4: "마흔", 5: "쉰",
                 6: "예순", 7: "일흔", 8: "여든", 9: "아흔"}.get(tens, "")
    ones_word = _KO_NATIVE_UNITS.get(ones, "")
    return f"{tens_word}{ones_word}"


def _say_number(n_str: str, lang: str) -> str:
    """순수 숫자 문자열을 말로. 정수면 그대로, 소수면 점 분리."""
    if "." in n_str:
        int_part, dec_part = n_str.split(".", 1)
        try:
            int_word = num2words(int(int_part), lang=lang)
        except Exception:
            int_word = int_part
        # 소수부는 자릿수마다 따로 읽음 (3.14 → 삼 점 일 사)
        digit_words = " ".join(num2words(int(d), lang=lang) for d in dec_part if d.isdigit())
        point = {"ko": "점", "ja": "てん", "en": "point"}.get(lang, "point")
        return f"{int_word} {point} {digit_words}".strip()
    try:
        return num2words(int(n_str.replace(",", "")), lang=lang)
    except Exception:
        return n_str


# --- 정규식 패턴 ---
# 한국어가 뒤에 붙어도 잡히도록 \b 대신 lookaround 사용.
# 시간 HH:MM (콜론으로 구분된 두 정수)
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
# 퍼센트
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# 통화: $123(.45), ₩900, 900원, 500엔, ¥1000  — 콤마 허용
_KRW_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\s*)(?:원|won)\b", re.IGNORECASE)
_USD_RE = re.compile(r"\$\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_JPY_RE = re.compile(r"(?:¥|￥)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*엔(?![가-힣])")
# 복합 단위(슬래시 포함)는 먼저 단독 정규식으로. 그 다음에 일반 단위.
_UNIT_COMPLEX_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(km/h|km/s|m/s)"
)
_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    # 긴 것부터 정렬 (kWh/GB 가 W/B 보다 먼저, km/cm/mm/kg/mg 가 m 보다 먼저)
    r"(kWh|개월|GB|MB|KB|TB|km|cm|mm|kg|mg|ml|L|W|m|"
    r"시간|회차|일자|분|초|일|주|년|월|살|세|회)"
    r"(?![A-Za-z0-9])"   # 영문/숫자가 뒤따르면 단위 끝이 아님 (한글은 OK)
)
# 한국어 카운터: 3개, 21번, 5명
_KO_COUNTER_RE = re.compile(r"(\d{1,2})\s*(" + "|".join(_KO_COUNTERS) + r")(?![가-힣])")
# 콤마 포함 큰 수 (1,234 / 900,000 등). 단어 경계 대신 숫자가 아닌 것 lookaround.
_COMMA_NUM_RE = re.compile(r"(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)")
# 4자리+ 정수 (년도 등). 한국어가 뒤에 와도 잡히게.
_BIG_INT_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")
# 소수
_DECIMAL_RE = re.compile(r"(?<!\d)\d+\.\d+(?!\d)")
# 남은 정수 (1~3자리)
_INT_RE = re.compile(r"(?<!\d)\d+(?!\d)")

# 단위 발음 매핑 (영문 단위 → 한글 음역)
_UNIT_SAY = {
    "km": "킬로미터", "cm": "센티미터", "mm": "밀리미터",
    "kg": "킬로그램", "mg": "밀리그램",
    "ml": "밀리리터", "L": "리터",
    "GB": "기가바이트", "MB": "메가바이트", "KB": "킬로바이트", "TB": "테라바이트",
    "km/h": "킬로미터 퍼 아워", "km/s": "킬로미터 퍼 세컨드", "m/s": "미터 퍼 세컨드",
    "kWh": "킬로와트시", "W": "와트", "m": "미터",
}


def normalize(text: str, lang: str = "ko") -> str:
    """TTS 직전 텍스트 정규화. 숫자/단위/기호를 발화 가능한 형태로 바꾼다."""
    if not text:
        return text
    t = text

    # 1) 시간: 10:30 → 열 시 삼십 분 (한국어 한정. 다른 언어는 그대로)
    if lang == "ko":
        def _time_sub(m):
            h, mn = int(m.group(1)), int(m.group(2))
            hour_word = _ko_native_number(h)   # 시각은 고유어 ('열 시')
            min_word = num2words(mn, lang="ko") if mn else ""
            return f"{hour_word} 시" + (f" {min_word} 분" if mn else " 정각")
        t = _TIME_RE.sub(_time_sub, t)

    # 2) 퍼센트
    def _pct_sub(m):
        n = _say_number(m.group(1), lang)
        pct = {"ko": "퍼센트", "ja": "パーセント", "en": "percent"}.get(lang, "percent")
        return f"{n} {pct}"
    t = _PCT_RE.sub(_pct_sub, t)

    # 3) 통화
    def _krw_sub(m):
        n = _say_number(m.group(1).replace(",", ""), lang)
        return f"{n} 원"
    t = _KRW_RE.sub(_krw_sub, t)

    def _usd_sub(m):
        n = _say_number(m.group(1), lang)
        unit = {"ko": "달러", "ja": "ドル", "en": "dollars"}.get(lang, "dollars")
        return f"{n} {unit}"
    t = _USD_RE.sub(_usd_sub, t)

    def _jpy_sub(m):
        digits = m.group(1) or m.group(2) or ""
        n = _say_number(digits, lang)
        unit = {"ko": "엔", "ja": "円", "en": "yen"}.get(lang, "yen")
        return f"{n} {unit}"
    t = _JPY_RE.sub(_jpy_sub, t)

    # 4a) 복합 단위 (km/h 등) — 일반 단위보다 먼저
    def _unit_complex_sub(m):
        n_raw, unit = m.group(1), m.group(2)
        n = _say_number(n_raw, lang) if lang == "ko" else _say_number(n_raw, lang)
        return f"{n} {_UNIT_SAY.get(unit, unit)}"
    t = _UNIT_COMPLEX_RE.sub(_unit_complex_sub, t)

    # 4b) 단일 단위 (영문/한국어 혼합)
    def _unit_sub(m):
        n_raw, unit = m.group(1), m.group(2)
        if lang == "ko":
            # 시간/분/초/년 등 한국어 단위는 시각/카운터로 따로 처리되기도 함
            # 일관성을 위해 한자어 발음 사용
            n = _say_number(n_raw, "ko")
        else:
            n = _say_number(n_raw, lang)
        unit_say = _UNIT_SAY.get(unit, unit)
        return f"{n} {unit_say}"
    t = _UNIT_RE.sub(_unit_sub, t)

    # 5) 한국어 카운터 (1~99 고유어)
    if lang == "ko":
        def _counter_sub(m):
            n = int(m.group(1))
            counter = m.group(2)
            if 1 <= n <= 99:
                return f"{_ko_native_number(n)} {counter}"
            return f"{_say_number(str(n), 'ko')} {counter}"
        t = _KO_COUNTER_RE.sub(_counter_sub, t)

    # 6) 콤마 포함 큰 수 (1,234 / 900,000)
    t = _COMMA_NUM_RE.sub(lambda m: _say_number(m.group(0), lang), t)

    # 7) 4자리+ 정수 (2025년 등)
    t = _BIG_INT_RE.sub(lambda m: _say_number(m.group(0), lang), t)

    # 8) 소수 (3.14)
    t = _DECIMAL_RE.sub(lambda m: _say_number(m.group(0), lang), t)

    # 9) 남은 정수 (1~3자리)
    t = _INT_RE.sub(lambda m: _say_number(m.group(0), lang), t)

    return t
