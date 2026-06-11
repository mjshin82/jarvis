"""IR 가전 제어 — eMotion Pro(LinknLink) MQTT 직결.

music.py 패턴을 따라 모듈 레벨 함수로 노출한다(llm.py 가 직접 import).
가전→토픽 매핑은 iot.yaml(고정 맵). config 로드(동기)와 connect(비동기 네트워크)를
분리한다 — LLM 툴 스펙 생성 시 가전 목록이 필요하므로 LLM() 전에 load_config() 가
끝나 있어야 한다.

eMotion Pro 의 실제 토픽/페이로드 포맷은 공개 문서에 없다. `/ir scan` 으로 실기기에서
관측한 토픽을 iot.yaml 에 채워 넣는다.
"""
import os

import yaml

import config

_appliances: dict = {}   # 키 → {"aliases": [...], "commands": {name: {topic, payload}}}


def load_config(path: str = None) -> None:
    """iot.yaml 파싱 → _appliances. 순수·동기. 파일 없으면 빈 맵."""
    global _appliances
    p = path or config.IOT_CONFIG_PATH
    _appliances = {}
    if not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            result = data.get("appliances", {}) or {}
        _appliances = result
    except Exception as e:
        print(f"[iot] iot.yaml 로드 실패: {e}")
        _appliances = {}


def list_appliances() -> list[str]:
    """등록된 가전 키 목록."""
    return list(_appliances.keys())


def commands_for(appliance: str) -> list[str]:
    """가전의 명령 이름 목록(미등록이면 빈 리스트)."""
    spec = _appliances.get(appliance)
    if not spec:
        return []
    return list((spec.get("commands") or {}).keys())


def resolve(name: str) -> str | None:
    """alias(또는 키)를 정규 가전 키로. 대소문자/양옆 공백 무시. 못 찾으면 None."""
    if not name:
        return None
    n = name.strip().lower()
    for key, spec in _appliances.items():
        if key.lower() == n:
            return key
        for a in (spec.get("aliases") or []):
            if str(a).strip().lower() == n:
                return key
    return None


def resolve_command(appliance: str, command: str, value=None):
    """(가전, 명령, 값) → (topic, payload). 못 찾으면 None.
    payload 안의 {value} 는 value 로 치환(없으면 빈 문자열)."""
    key = resolve(appliance)
    if key is None:
        return None
    cmds = (_appliances.get(key) or {}).get("commands") or {}
    spec = cmds.get(command)
    if not spec:
        return None
    topic = spec.get("topic")
    if not topic:
        return None
    payload = str(spec.get("payload", ""))
    payload = payload.replace("{value}", "" if value is None else str(value))
    return topic, payload
