import appliance_intent as ai

ALIASES = {"aircon": ["에어컨", "에어콘"], "tv": ["티비", "TV"]}


def test_power_on():
    assert ai.classify("에어컨 켜줘", ALIASES) == ("aircon", "power", None)
    assert ai.classify("티비 켜줘", ALIASES) == ("tv", "power", None)


def test_power_off_maps_to_power():
    # 단일 power 토글 명령만 있는 가전: 꺼줘도 power 로
    assert ai.classify("에어컨 꺼줘", ALIASES) == ("aircon", "power", None)


def test_set_temp():
    assert ai.classify("에어컨 26도", ALIASES) == ("aircon", "set_temp", 26)
    assert ai.classify("에어컨 24도로 해줘", ALIASES) == ("aircon", "set_temp", 24)


def test_no_appliance_returns_none():
    assert ai.classify("오늘 날씨 어때", ALIASES) is None


def test_unknown_alias_returns_none():
    assert ai.classify("냉장고 켜줘", ALIASES) is None


def test_long_utterance_deferred_to_llm():
    long = "에어컨 좀 켜고 싶은데 지금 너무 더워서 그런데 혹시 26도 정도로 맞춰줄 수 있을까"
    assert ai.classify(long, ALIASES) is None


def test_three_digit_number_deferred():
    # 비정상 온도(3자리 이상)는 set_temp 로 잘못 잡지 말고 LLM 에 위임
    assert ai.classify("에어컨 100도", ALIASES) is None


def test_empty_aliases_returns_none():
    assert ai.classify("에어컨 켜줘", {}) is None
