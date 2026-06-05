# tests/test_remote_mic_config.py
import importlib
import config


def test_remote_mic_defaults():
    importlib.reload(config)
    assert config.REMOTE_MIC_ENABLED is False
    assert config.REMOTE_MIC_KEY == "jarvis"
    assert config.REMOTE_MIC_IDLE_S == 2.0
