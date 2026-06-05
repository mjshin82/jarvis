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
