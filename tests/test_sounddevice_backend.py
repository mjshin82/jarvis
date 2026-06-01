import asyncio
import audio_backend as ab

def test_sounddevice_backend_music_delegates_to_music_module(monkeypatch):
    calls = {}
    async def fake_play(q): calls["play"] = q; return f"play:{q}"
    async def fake_stop(): calls["stop"] = True; return "stop"
    monkeypatch.setattr(ab, "_chrome_play_music", fake_play, raising=False)
    monkeypatch.setattr(ab, "_chrome_stop_music", fake_stop, raising=False)

    async def main():
        be = ab.SounddeviceBackend()
        assert be.supports_inapp_audio is False
        assert (await be.play_music("아이유")) == "play:아이유"
        assert (await be.stop_music()) == "stop"
    asyncio.run(main())
