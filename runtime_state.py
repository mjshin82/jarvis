# runtime_state.py
"""jarvis 런타임 상태 영속 — 재시작 시 마지막 지속 모드(회의/번역) 복구용.
setting.yaml(사용자 설정)과 별개. gitignore 된 .jarvis_state.json."""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".jarvis_state.json")
_ALLOWED = {"idle", "meeting", "translate"}
_last = None   # 중복 기록 방지(같은 값 연속 저장 스킵)


def save_mode(mode: str, path: str = None) -> None:
    global _last
    if mode not in _ALLOWED or mode == _last:
        return
    _last = mode
    try:
        with open(path or PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": mode}, f)
    except Exception:
        pass


def load_mode(path: str = None) -> str:
    p = path or PATH
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                m = (json.load(f) or {}).get("mode")
            if m in _ALLOWED:
                return m
    except Exception:
        pass
    return "idle"
