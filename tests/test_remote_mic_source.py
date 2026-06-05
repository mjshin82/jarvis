# tests/test_remote_mic_source.py
import numpy as np
import config
from mic_source import RemoteMicSource


def test_feed_rechunks_to_block_size_and_scales():
    out = []
    src = RemoteMicSource(sink=out.append)
    # int16 341개(512 미만) → 아직 방출 없음
    src.feed(np.zeros(341, dtype=np.int16).tobytes())
    assert out == []
    # 추가 1480개 → 합 1821 = 512*3 + 285 → 3블록 방출, 285 잔여
    src.feed((np.ones(1480, dtype=np.int16) * 16384).tobytes())
    assert len(out) == 3
    assert all(b.shape == (config.BLOCK_SIZE,) for b in out)
    assert all(b.dtype == np.float32 for b in out)
    # 16384/32768 = 0.5 로 스케일된 값이 두 번째 이후 블록에 존재
    assert np.isclose(out[-1][-1], 0.5, atol=1e-3)


def test_reset_clears_partial_buffer():
    out = []
    src = RemoteMicSource(sink=out.append)
    src.feed(np.ones(300, dtype=np.int16).tobytes())   # 잔여 300
    src.reset()
    src.feed(np.ones(512, dtype=np.int16).tobytes())   # reset 후 정확히 1블록
    assert len(out) == 1


def test_feed_tolerates_odd_byte_and_empty():
    out = []
    src = RemoteMicSource(sink=out.append)
    src.feed(b"")                 # 빈 입력 → 무방출, 예외 없음
    src.feed(b"\x01")             # 홀수 1바이트 → 버려짐, 예외 없음
    assert out == []
    # 512 샘플 = 1024바이트 + 끝에 홀수 1바이트 → 정확히 1블록
    pcm = (np.ones(512, dtype=np.int16) * 100).tobytes() + b"\x07"
    src.feed(pcm)
    assert len(out) == 1
