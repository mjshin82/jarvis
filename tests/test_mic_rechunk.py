import asyncio

import numpy as np

import audio_backend as ab
import config


def test_mic_frames_rechunks_to_block_size():
    """데몬의 가변 크기 마이크 프레임을 정확히 BLOCK_SIZE(512) 로 재청크해야 한다."""
    async def main():
        be = ab.AECBackend(cmd=["true"])   # 기동하지 않음; 큐만 직접 사용
        be._mic_q = asyncio.Queue()
        await be._mic_q.put(np.zeros(341, np.float32))    # 512 미만
        await be._mic_q.put(np.ones(1480, np.float32))    # 합 1821 → 512*3=1536, 285 잔여
        out = []
        async def read():
            async for blk in be.mic_frames():
                out.append(blk)
                if len(out) == 3:
                    return
        await asyncio.wait_for(read(), timeout=2)
        assert len(out) == 3
        assert all(b.shape == (config.BLOCK_SIZE,) for b in out)
    asyncio.run(main())
