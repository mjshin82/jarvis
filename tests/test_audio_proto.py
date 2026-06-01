import numpy as np
import audio_proto as p

def test_encode_decode_roundtrip_multiple_frames():
    data = (
        p.encode(p.FLUSH_VOICE)
        + p.encode_event({"voice": "drained"})
        + p.encode_pcm(p.MIC, np.array([0.1, -0.2, 0.3], dtype=np.float32))
    )
    dec = p.FrameDecoder()
    dec.feed(data)
    frames = list(dec)
    assert [f[0] for f in frames] == [p.FLUSH_VOICE, p.EVENT, p.MIC]
    assert frames[0][1] == b""
    assert p.decode_event(frames[1][1]) == {"voice": "drained"}
    np.testing.assert_allclose(p.pcm_to_array(frames[2][1]),
                               np.array([0.1, -0.2, 0.3], dtype=np.float32), rtol=1e-6)

def test_partial_feed_waits_for_full_frame():
    full = p.encode_pcm(p.MIC, np.array([1.0, 2.0], dtype=np.float32))
    dec = p.FrameDecoder()
    dec.feed(full[:3])
    assert list(dec) == []
    dec.feed(full[3:])
    frames = list(dec)
    assert len(frames) == 1 and frames[0][0] == p.MIC
