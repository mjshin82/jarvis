import asyncio
import numpy as np
import music

def test_ffmpeg_pump_feeds_chunks_and_stops(monkeypatch):
    import sys
    fake_ffmpeg = [sys.executable, "-c",
        "import sys,numpy as np;"
        "sys.stdout.buffer.write(np.zeros(9600,np.float32).tobytes());"
        "sys.stdout.buffer.flush()"]
    monkeypatch.setattr(music, "_ffmpeg_cmd", lambda vid: fake_ffmpeg)

    got = []
    async def sink(pcm): got.append(len(pcm))

    async def main():
        handle = await music.start_ffmpeg_pump("dummyid", sink)
        await asyncio.sleep(0.5)
        await music.stop_ffmpeg_pump(handle)
        assert sum(got) == 9600
    asyncio.run(main())
