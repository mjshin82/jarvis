# settings.py
"""사용자 설정(미팅 번역/STT 백엔드) — setting.yaml 영속.
웹 설정 팝업이 편집(apply), 콘솔은 /reload-settings 로 재로드(load)만."""
import os
import yaml
import config

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setting.yaml")

DEFAULTS = {
    "stt_backend": "gladia",                                                  # gladia | local
    "llm_backend": "local" if config.LLM_BACKEND == "local" else "deepseek",  # deepseek | local
}
ALLOWED = {
    "stt_backend": {"gladia", "local"},
    "llm_backend": {"deepseek", "local"},
}

_current = dict(DEFAULTS)


def current() -> dict:
    return dict(_current)


def get(key: str):
    return _current.get(key, DEFAULTS.get(key))


def _valid(updates: dict) -> dict:
    out = {}
    for k, v in (updates or {}).items():
        if k in ALLOWED and v in ALLOWED[k]:
            out[k] = v
    return out


def save(path: str = None) -> None:
    with open(path or PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(_current, f, allow_unicode=True, sort_keys=True)


def load(path: str = None) -> dict:
    """파일 읽어 _current 갱신(기본값 위 병합). 없으면 기본값으로 생성."""
    global _current
    p = path or PATH
    _current = dict(DEFAULTS)
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # 구버전 키 이주: llm_backend 없으면 옛 키(conversation_llm/translate)에서 가져옴.
            if "llm_backend" not in data:
                old = data.get("conversation_llm_backend") or data.get("translate_backend")
                if old:
                    data["llm_backend"] = old
            _current.update(_valid(data))
        else:
            save(p)
    except Exception:
        _current = dict(DEFAULTS)
    return current()


def apply(updates: dict, path: str = None) -> dict:
    """유효한 키/값만 갱신 후 저장."""
    _current.update(_valid(updates))
    save(path)
    return current()
