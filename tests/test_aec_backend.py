import asyncio
import sys
import numpy as np
import audio_backend as ab


def test_is_speaking_counter_invariant():
    be = ab.AECBackend(cmd=["true"])   # not started; just exercise counters
    be._voice_sent = 0; be._voice_done = 0
    be._voice_sent += 1; be._voice_sent += 1     # 2 chunks "sent"
    assert be.is_speaking() is True
    be._handle_event({"vc": 1})                  # 1 done
    assert be.is_speaking() is True               # still speaking (2>1)
    be._handle_event({"vc": 2})                  # all done
    assert be.is_speaking() is False
    be._voice_sent += 1                           # new chunk
    assert be.is_speaking() is True
    be.flush_voice = lambda: None                 # avoid touching stdin in this unit test


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
