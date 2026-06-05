# tests/test_local_mic_source.py
import numpy as np
from mic_source import LocalMicSource


def test_callback_forwards_mono_copy_to_sink():
    out = []
    src = LocalMicSource(sink=out.append)
    # sounddevice 콜백 모사: (frames, channels) float32, 1채널만 추출되어야
    indata = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)
    src._callback(indata, 3, None, None)
    assert len(out) == 1
    assert np.array_equal(out[0], np.array([0.1, 0.2, 0.3], dtype=np.float32))
    # 복사본이어야 한다 (원본 변경이 sink 결과에 영향 없음)
    indata[0, 0] = 9.0
    assert out[0][0] == np.float32(0.1)
