import asyncio
import numpy as np
from tests.fake_backend import FakeBackend

def test_fake_backend_mic_and_voice():
    async def main():
        be = FakeBackend(mic_script=[np.zeros(512, np.float32), np.ones(512, np.float32)])
        await be.start()
        got = []
        async for blk in be.mic_frames():
            got.append(blk)
        assert len(got) == 2
        await be.play_voice(np.zeros(10, np.float32), 44100)
        assert be.is_speaking() is True
        be.flush_voice()
        assert be.is_speaking() is False
        assert "play" in (await be.play_music("아이유"))
        assert "stop" in (await be.stop_music())
        await be.close()
    asyncio.run(main())
