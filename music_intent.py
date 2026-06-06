"""Fast-path 의도 분류기 — 명백한 발화는 LLM 없이 코드가 곧장 처리.

LLM 도구 호출(1차: 비스트리밍 도구 감지 + 2차: 결과 기반 답변)을 두 번 거치는
대신, 발화 그 자체로 의도가 명확한 경우만 골라 곧장 분기한다. 모호하면 None 을
돌려 LLM 흐름으로 넘긴다 — 즉 보수적으로 매칭한다(False positive 가 거짓 응답을
부르므로 false negative 가 더 안전).

지원 의도:
  music_stop : '음악 꺼줘', '꺼줘', '음악 멈춰' 등
  music_play : '음악 틀어줘', '뉴진스 틀어줘', '재즈 들려줘' 등 (검색어 추출)
"""
import re

# --- 음악 정지 ---
# 짧고 의도가 분명한 표현만. '음악'을 안 붙여도 'play_music' 후가 명백한 케이스 포함.
_STOP_PATTERNS = (
    r"^\s*음악\s*(꺼줘|꺼|멈춰|중지|정지|스톱|stop)\s*\.?\s*$",
    r"^\s*(꺼줘|꺼|멈춰|중지|정지|스톱)\s*\.?\s*$",
    r"^\s*music\s*(off|stop)\s*\.?\s*$",
    r"^\s*(노래|뮤직)\s*(꺼줘|꺼|멈춰|중지|정지)\s*\.?\s*$",
)

# --- 음악 재생 ---
# "(<검색어>) 틀어줘/들려줘/재생해줘" 형태. 검색어는 캡처 그룹으로 뽑는다.
# 너무 짧은 발화(예: '틀어줘'만)는 검색어가 없어 매칭 안 됨 → LLM 에 넘김.
_PLAY_PATTERNS = (
    # (... ) 음악/노래 (틀어줘/들려줘/재생)
    re.compile(r"^\s*(.+?)\s*(?:노래|음악|뮤직|곡)\s*(?:좀\s*)?(?:틀어줘|틀어|들려줘|들려|재생해줘|재생|플레이)\s*\.?\s*$"),
    # (... ) (틀어줘/들려줘/재생)
    re.compile(r"^\s*(.+?)\s*(?:좀\s*)?(?:틀어줘|틀어|들려줘|들려|재생해줘|재생|플레이)\s*\.?\s*$"),
    # play (...)
    re.compile(r"^\s*play\s+(.+?)\s*\.?\s*$", re.IGNORECASE),
)

# 검색어로 잡혔지만 사실은 명령 자체인 단어 — 검색어가 너무 짧거나 이런 단어면 무효.
_BAD_QUERY_TOKENS = {"음악", "노래", "뮤직", "곡", "그거", "그것", "다시", "한곡", "한 곡"}


def classify(text: str) -> tuple[str, dict] | None:
    """발화를 분류해 (intent, params) 반환. 매칭 안 되면 None.
      ('music_stop', {})
      ('music_play', {'query': '뉴진스'})"""
    if not text:
        return None
    t = text.strip()
    # 너무 긴 발화는 의도가 섞여 있을 가능성 → LLM 에 맡김
    if len(t) > 40:
        return None

    # 음악 정지
    low = t.lower()
    for pat in _STOP_PATTERNS:
        if re.match(pat, low):
            return ("music_stop", {})

    # 음악 재생: 검색어 캡처
    for pat in _PLAY_PATTERNS:
        m = pat.match(t)
        if not m:
            continue
        query = m.group(1).strip().strip(",.!?")
        # 검색어 검증: 너무 짧거나 무의미하면 무효
        if not query or len(query) < 2:
            continue
        if query in _BAD_QUERY_TOKENS:
            continue
        return ("music_play", {"query": query})

    return None
