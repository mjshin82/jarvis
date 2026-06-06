# tests/test_gladia_stt.py
import gladia_stt


def _gl(partials, finals):
    return gladia_stt.GladiaSTT("k", on_partial=lambda t: partials.append(t),
                                on_final=lambda t: finals.append(t), on_log=lambda *a: None)


def _msg(text, is_final=False):
    return {"type": "transcript", "data": {"is_final": is_final, "utterance": {"text": text}}}


def test_partial_emits():
    p, f = [], []
    _gl(p, f)._handle_gladia_message(_msg("안녕"))
    assert p == ["안녕"] and f == []


def test_final_emits():
    p, f = [], []
    _gl(p, f)._handle_gladia_message(_msg("안녕하세요", is_final=True))
    assert f == ["안녕하세요"] and p == []


def test_non_transcript_and_empty_ignored():
    p, f = [], []
    gl = _gl(p, f)
    gl._handle_gladia_message({"type": "speech_start"})
    gl._handle_gladia_message(_msg(""))
    assert p == [] and f == []


def test_feed_block_converts_float32_and_enqueues():
    import numpy as np
    from gladia_stt import GladiaSTT
    g = GladiaSTT("k", on_partial=lambda t: None, on_final=lambda t: None)
    g.feed_block(np.array([0.0, 1.0, -1.0], dtype=np.float32))
    assert g._out_q.qsize() == 1
    arr = np.frombuffer(g._out_q.get_nowait(), dtype="<i2")
    assert arr[1] == 32767 and arr[2] == -32767


def test_config_custom_vocabulary():
    from gladia_stt import GladiaSTT
    g = GladiaSTT("k", languages=("ko", "en"), on_partial=lambda t: None,
                  on_final=lambda t: None, vocabulary=["신명진"])
    cfg = g._config()
    vocab = cfg["realtime_processing"]["custom_vocabulary_config"]["vocabulary"]
    assert vocab[0]["value"] == "신명진"
    assert vocab[0]["language"] == "ko"


def test_config_no_vocabulary_omits_realtime_processing():
    from gladia_stt import GladiaSTT
    g = GladiaSTT("k", on_partial=lambda t: None, on_final=lambda t: None)
    assert "realtime_processing" not in g._config()
