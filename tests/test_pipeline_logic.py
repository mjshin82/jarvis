import asyncio
import numpy as np
import audio_io
from tests.fake_backend import FakeBackend

class FakeVAD:
    def __init__(self, script): self.script=list(script); self.i=0; self.threshold=0.5
    def __call__(self, b):
        ev = self.script[self.i] if self.i < len(self.script) else None
        self.i += 1; return ev
    def reset_states(self): pass

S={'start':0.9}; E={'end':1.0}; N=None

async def collect(mic, script, wake_block=None, speaking=False, n=None):
    mic._vad = FakeVAD(script)
    nb = n or len(script)
    be = mic.backend
    be.mic_script = [np.zeros(audio_io.config.BLOCK_SIZE, np.float32) for _ in range(nb)]
    calls={'i':0}
    def wake(b):
        calls['i']+=1; return wake_block is not None and calls['i']==wake_block
    out=[]
    async for kind,_ in mic.events(wake_detect=wake, is_speaking=lambda: speaking):
        out.append(kind)
    return out

def test_wake_discards_pending_utterance():
    async def main():
        be = FakeBackend(); await be.start()
        mic = audio_io.Microphone(be)
        out = await collect(mic, [S,N,E,N], wake_block=2)
        assert "wake" in out and "utterance" not in out
    asyncio.run(main())

def test_normal_capture():
    async def main():
        be = FakeBackend(); await be.start()
        mic = audio_io.Microphone(be)
        out = await collect(mic, [S,N,E], wake_block=None)
        assert out == ["start","utterance"]
    asyncio.run(main())
