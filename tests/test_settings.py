# tests/test_settings.py
import os
import yaml
import settings


def test_defaults():
    assert settings.DEFAULTS["stt_backend"] == "gladia"
    assert settings.DEFAULTS["llm_backend"] in ("deepseek", "local")
    assert "translate_backend" not in settings.DEFAULTS
    assert "conversation_stt_backend" not in settings.DEFAULTS
    assert "conversation_llm_backend" not in settings.DEFAULTS


def test_load_creates_file_with_defaults(tmp_path):
    p = str(tmp_path / "setting.yaml")
    cur = settings.load(p)
    assert cur == settings.DEFAULTS
    assert os.path.exists(p)


def test_apply_filters_invalid_and_persists(tmp_path):
    p = str(tmp_path / "setting.yaml")
    settings.load(p)
    cur = settings.apply({"llm_backend": "local", "stt_backend": "bogus"}, p)
    assert cur["llm_backend"] == "local"
    assert cur["stt_backend"] == "gladia"   # 무효값 무시 → 기본 유지
    settings.load(p)
    assert settings.get("llm_backend") == "local"


def test_apply_ignores_legacy_keys(tmp_path):
    p = str(tmp_path / "setting.yaml")
    settings.load(p)
    cur = settings.apply({"translate_backend": "local", "conversation_stt_backend": "gladia"}, p)
    assert "translate_backend" not in cur
    assert "conversation_stt_backend" not in cur


def test_migrates_legacy_conversation_llm_key(tmp_path):
    p = str(tmp_path / "setting.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump({"conversation_llm_backend": "local", "stt_backend": "local"}, f)
    settings.load(p)
    assert settings.get("llm_backend") == "local"
    assert settings.get("stt_backend") == "local"


def test_migrates_translate_backend_when_no_conv_llm(tmp_path):
    p = str(tmp_path / "setting.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump({"translate_backend": "local"}, f)
    settings.load(p)
    assert settings.get("llm_backend") == "local"
