import config
from llm import LLM


def test_set_backend_local(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "remote")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-real")
    llm = LLM()
    llm.set_backend("local")
    assert llm.backend == "local"
    assert llm.model == config.LOCAL_MODEL
    assert llm.extra.get("keep_alive") == config.OLLAMA_KEEP_ALIVE


def test_set_backend_deepseek_with_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "remote")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-real")
    llm = LLM()
    llm.set_backend("deepseek")
    assert llm.backend == "remote"
    assert llm.model == config.DEEPSEEK_MODEL
    assert llm.extra == {}


def test_deepseek_falls_back_to_local_without_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "remote")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    llm = LLM()
    llm.set_backend("deepseek")
    assert llm.backend == "local"


def test_mock_ignores_set_backend(monkeypatch):
    monkeypatch.setattr(config, "LLM_BACKEND", "mock")
    llm = LLM()
    llm.set_backend("deepseek")
    assert llm.client is None and llm.backend == "mock"
