# tests/test_intent.py
from intent import mode_intent


def test_enter_intents():
    assert mode_intent("미팅모드로 변경해줘") == "meeting"
    assert mode_intent("회의 모드 시작") == "meeting"
    assert mode_intent("회의 들어가자") == "meeting"
    assert mode_intent("meeting 모드로 전환") == "meeting"


def test_stop_intents():
    assert mode_intent("회의 끝내줘") == "stop"
    assert mode_intent("회의 종료") == "stop"
    assert mode_intent("회의 나가자") == "stop"


def test_non_intents():
    assert mode_intent("오늘 회의 자료 요약해줘") is None
    assert mode_intent("안녕 자비스") is None
    assert mode_intent("") is None
    assert mode_intent(None) is None
