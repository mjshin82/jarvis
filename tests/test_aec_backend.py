import asyncio
import sys
import numpy as np
import audio_backend as ab

def test_aec_backend_against_fake_daemon():
    async def main():
        be = ab.AECBackend(cmd=[sys.executable, "tests/fake_daemon.py"])
        await be.start()
        frames = []
        async def read_three():
            async for blk in be.mic_frames():
                frames.append(blk)
                if len(frames) == 3:
                    return
        await asyncio.wait_for(read_three(), timeout=5)
        assert len(frames) == 3
        await be.play_voice(np.zeros(48000, np.float32), 48000)
        assert be.is_speaking() is True
        await asyncio.sleep(0.3)
        assert be.is_speaking() is False
        await be.close()
    asyncio.run(main())
