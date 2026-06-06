import asyncio
from llm import LLM


def test_summarize_mock_returns_empty(monkeypatch):
    monkeypatch.setattr("config.LLM_BACKEND", "mock", raising=False)
    llm = LLM()
    llm._mock = True
    out = asyncio.run(llm.summarize("아무 회의 내용"))
    assert out == ""


def test_summarize_calls_client():
    llm = LLM()
    llm._mock = False
    captured = {}

    class FakeMsg:
        content = "요약본"

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        async def create(self, **kw):
            captured.update(kw)
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    llm.client = FakeClient()
    llm.model = "m"
    llm.extra = {}
    out = asyncio.run(llm.summarize("회의 원문", "Japanese"))
    assert out == "요약본"
    assert "Japanese" in captured["messages"][0]["content"]
    assert "회의 원문" in captured["messages"][-1]["content"]
