import runtime_state


def test_save_load_roundtrip(tmp_path):
    runtime_state._last = None              # dedupe 상태 초기화
    p = str(tmp_path / "s.json")
    runtime_state.save_mode("meeting", path=p)
    assert runtime_state.load_mode(path=p) == "meeting"


def test_load_default_idle_when_missing(tmp_path):
    assert runtime_state.load_mode(path=str(tmp_path / "none.json")) == "idle"


def test_invalid_mode_not_written(tmp_path):
    runtime_state._last = None
    p = str(tmp_path / "s.json")
    runtime_state.save_mode("bogus", path=p)
    assert runtime_state.load_mode(path=p) == "idle"   # 기록 안 됨 → 기본값


def test_dedupe_skips_unchanged(tmp_path):
    runtime_state._last = None
    p = str(tmp_path / "s.json")
    runtime_state.save_mode("translate", path=p)
    import os
    mtime1 = os.path.getmtime(p)
    runtime_state.save_mode("translate", path=p)   # 동일값 → 재기록 안 함
    assert os.path.getmtime(p) == mtime1
