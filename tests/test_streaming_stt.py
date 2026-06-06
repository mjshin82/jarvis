import numpy as np
from realtime_stt import RealtimeSTTAdapter
from streaming_stt import StreamingRecognizer


class _FakeRecorder:
    def __init__(self): self.fed = []
    def feed_audio(self, pcm, sr): self.fed.append((pcm, sr))


def test_is_adapter_subclass_with_silence_flush():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    assert isinstance(rx, RealtimeSTTAdapter)
    assert rx._silence_flush is True
    assert rx.language == "ko"


def test_feed_block_delegates_to_adapter():
    rx = StreamingRecognizer(on_partial=lambda t: None, on_final=lambda t: None)
    rx.recorder = _FakeRecorder()
    rx.feed_block(np.array([0.0, 1.0], dtype=np.float32))
    assert len(rx.recorder.fed) == 1
