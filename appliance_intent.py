"""Fast-path 의도 분류기(IR 가전) — music_intent 와 동형.

명백한 발화만 (가전, 명령, 값) 으로 분기하고, 모호하면 None 을 돌려 LLM 흐름에 맡긴다.
가전 alias 는 호출자가 iot.yaml 에서 읽어 넘긴다(테스트 가능하도록 인자화).
보수적으로 매칭한다(false positive 가 거짓 동작을 부르므로).
"""
import re

_POWER = re.compile(r"(켜줘|켜|꺼줘|꺼|틀어줘|틀어|꺼주라|켜주라)\s*\.?\s*$")
_TEMP = re.compile(r"(\d{1,2})\s*도")


def _find_appliance(text: str, aliases: dict) -> str | None:
    """발화에 등장하는 가전 키. 가장 먼저 매칭되는 것."""
    for key, names in aliases.items():
        cands = [key] + list(names or [])
        for c in cands:
            if str(c).lower() in text.lower():
                return key
    return None


def classify(text: str, aliases: dict):
    """(appliance_key, command, value) 또는 None."""
    if not text:
        return None
    t = text.strip()
    if len(t) > 25:          # 길면 의도가 섞임 → LLM
        return None
    appliance = _find_appliance(t, aliases)
    if appliance is None:
        return None
    # 온도 지정: "에어컨 26도"
    m = _TEMP.search(t)
    if m:
        return (appliance, "set_temp", int(m.group(1)))
    # 전원: "켜줘/꺼줘"
    if _POWER.search(t):
        return (appliance, "power", None)
    return None
