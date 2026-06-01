import audio_backend as ab

def test_off_returns_sounddevice(monkeypatch):
    monkeypatch.setattr(ab.config, "AEC", "off")
    assert isinstance(ab.make_backend(), ab.SounddeviceBackend)

def test_auto_non_macos_returns_sounddevice(monkeypatch):
    monkeypatch.setattr(ab.config, "AEC", "auto")
    monkeypatch.setattr(ab.platform, "system", lambda: "Linux")
    assert isinstance(ab.make_backend(), ab.SounddeviceBackend)

def test_auto_macos_with_daemon_returns_aec(monkeypatch):
    monkeypatch.setattr(ab.config, "AEC", "auto")
    monkeypatch.setattr(ab.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ab, "_aec_available", lambda: True)
    assert isinstance(ab.make_backend(), ab.AECBackend)
