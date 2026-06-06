import asyncio
import coach


class _FakeResp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        self._content = content

    @property
    def chat(self):
        outer = self
        comp = type("Comp", (), {
            "create": staticmethod(lambda **kw: _coro(_FakeResp(outer._content)))
        })()
        return type("Chat", (), {"completions": comp})()


async def _coro(v):
    return v


def test_translate_multi_parses_json():
    c = _FakeClient('{"en": "hello", "ja": "こんにちは"}')
    out = asyncio.run(coach.translate_multi(c, "m", "안녕", "sys"))
    assert out == {"en": "hello", "ja": "こんにちは"}


def test_translate_multi_handles_code_fence():
    c = _FakeClient('```json\n{"en": "hi"}\n```')
    out = asyncio.run(coach.translate_multi(c, "m", "안녕", "sys"))
    assert out == {"en": "hi"}


def test_translate_multi_bad_output_returns_empty():
    c = _FakeClient("not json at all")
    out = asyncio.run(coach.translate_multi(c, "m", "안녕", "sys"))
    assert out == {}


def test_translate_multi_empty_text():
    c = _FakeClient('{"en": "x"}')
    out = asyncio.run(coach.translate_multi(c, "m", "   ", "sys"))
    assert out == {}


def test_build_multi_system_prompt_lists_langs():
    p = coach.build_multi_system_prompt(["Korean", "Japanese"], ["ko", "ja"], "ctx", ["Concode"])
    assert "Korean" in p and "Japanese" in p and "Concode" in p
    assert "ko, ja" in p          # 허용 코드만 명시(전체 4코드 아님)


def test_build_bilingual_system_prompt_names_both_directions():
    p = coach.build_bilingual_system_prompt(["Korean", "English"], ["ko", "en"], "ctx", ["Concode"])
    assert "Korean" in p and "English" in p
    assert "ko" in p and "en" in p
    assert "Concode" in p
    # 양방향(상대 언어로) 지향이 명시되어야 함
    assert "OTHER" in p or "the other" in p.lower()
