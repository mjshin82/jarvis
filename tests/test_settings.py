# tests/test_settings.py
import os
import settings


def test_defaults():
    assert settings.DEFAULTS["translate_backend"] == "deepseek"
    assert settings.DEFAULTS["stt_backend"] == "deepgram"


def test_load_creates_file_with_defaults(tmp_path):
    p = str(tmp_path / "setting.yaml")
    cur = settings.load(p)
    assert cur == settings.DEFAULTS
    assert os.path.exists(p)


def test_apply_filters_invalid_and_persists(tmp_path):
    p = str(tmp_path / "setting.yaml")
    settings.load(p)
    cur = settings.apply({"translate_backend": "local", "stt_backend": "bogus"}, p)
    assert cur["translate_backend"] == "local"
    assert cur["stt_backend"] == "deepgram"   # 무효값 무시 → 기본 유지
    settings.load(p)                           # 재로드해도 저장됨
    assert settings.get("translate_backend") == "local"
