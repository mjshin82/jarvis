# tests/test_deepgram_stt.py
import deepgram_stt


def _dg(partials, finals):
    return deepgram_stt.DeepgramSTT(
        "k", on_partial=lambda t: partials.append(t),
        on_final=lambda t: finals.append(t), on_log=lambda *a: None,
    )


def _msg(transcript, is_final=False, speech_final=False):
    return {"type": "Results", "is_final": is_final, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": transcript}]}}


def test_interim_emits_partial():
    p, f = [], []
    dg = _dg(p, f)
    dg._handle_dg_message(_msg("안녕"))
    assert p == ["안녕"] and f == []


def test_final_accumulate_then_speech_final():
    p, f = [], []
    dg = _dg(p, f)
    dg._handle_dg_message(_msg("안녕", is_final=True))
    dg._handle_dg_message(_msg("하세요", is_final=True, speech_final=True))
    assert f == ["안녕 하세요"]
    dg._handle_dg_message(_msg("또", is_final=True, speech_final=True))   # 새 발화
    assert f[-1] == "또"


def test_non_results_and_empty_ignored():
    p, f = [], []
    dg = _dg(p, f)
    dg._handle_dg_message({"type": "Metadata"})
    dg._handle_dg_message(_msg(""))
    assert p == [] and f == []
