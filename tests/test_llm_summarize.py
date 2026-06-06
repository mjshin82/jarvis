import asyncio
import config
from llm import LLM


def test_summarize_mock_returns_empty(monkeypatch):
    monkeypatch.setattr("config.LLM_BACKEND", "mock", raising=False)
    llm = LLM()
    llm._mock = True
    out = asyncio.run(llm.summarize("아무 회의 내용"))
    assert out == ""


def _fake_client(captured):
    class FakeMsg: content = "요약본"
    class FakeChoice: message = FakeMsg()
    class FakeResp: choices = [FakeChoice()]
    class FakeCompletions:
        async def create(self, **kw): captured.update(kw); return FakeResp()
    class FakeChat: completions = FakeCompletions()
    class FakeClient: chat = FakeChat()
    return FakeClient()


def test_summarize_uses_deepseek_v4pro_with_thinking(monkeypatch):
    import config
    from llm import LLM
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k", raising=False)
    monkeypatch.setattr(config, "SUMMARY_MODEL", "sum-model", raising=False)
    llm = LLM(); llm._mock = False
    captured = {}
    llm._summary_client = _fake_client(captured)     # lazy 빌드 대신 주입
    out = asyncio.run(llm.summarize("회의 원문", "Japanese"))
    assert out == "요약본"
    assert captured["model"] == "sum-model"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "Japanese" in captured["messages"][0]["content"]
    assert "Markdown" in captured["messages"][0]["content"]
    assert "회의 원문" in captured["messages"][-1]["content"]


def test_summarize_falls_back_to_local_without_deepseek(monkeypatch):
    import config
    from llm import LLM
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    llm = LLM(); llm._mock = False
    captured = {}
    llm.client = _fake_client(captured); llm.model = "local-m"; llm.extra = {}
    out = asyncio.run(llm.summarize("회의 원문", "Korean"))
    assert out == "요약본"
    assert captured["model"] == "local-m"
    assert captured["extra_body"] == {}
