"""음성/텍스트의 모드 전환 의도 매칭 (키워드 기반, 순수 함수).

회의-명사 + (종료/전환)-동사 가 함께 있을 때만 매칭 → 일반 대화 오탐 최소화.
"""
_MEETING_NOUNS = ("회의", "미팅", "meeting")
_STOP_VERBS = ("끝", "종료", "나가", "중지", "꺼")
_ENTER_VERBS = ("전환", "변경", "시작", "들어가", "열어", "켜", "바꿔")


def mode_intent(text):
    """text → "meeting" | "stop" | None.
    종료 동사를 먼저 검사(예: '회의 끝내줘'). 명사+동사 둘 다 있어야 매칭."""
    t = (text or "").lower()
    if not any(n in t for n in _MEETING_NOUNS):
        return None
    if any(v in t for v in _STOP_VERBS):
        return "stop"
    if any(v in t for v in _ENTER_VERBS):
        return "meeting"
    return None
