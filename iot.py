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

_client = None        # 살아있는 aiomqtt.Client (없으면 None)
_ready = False        # 연결 준비 완료 여부
_task = None          # 백그라운드 연결 유지 태스크


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


def available() -> bool:
    return _ready and _client is not None


async def send(appliance: str, command: str, value=None) -> str:
    """가전 명령을 publish. 사용자용 결과 문자열 반환(play_music 과 동형)."""
    if not available():
        return "IoT가 비활성 상태예요(브로커 연결 안 됨)."
    resolved = resolve_command(appliance, command, value)
    if resolved is None:
        return f"'{appliance} {command}' 명령을 모르겠어요."
    topic, payload = resolved
    try:
        await _client.publish(topic, payload=payload)
    except Exception as e:
        print(f"[iot] publish 실패: {e}")
        return "명령을 못 보냈어요."
    key = resolve(appliance)
    val = f" {value}" if value is not None else ""
    return f"{key} {command}{val} 처리했어요."


async def connect() -> None:
    """브로커 연결을 백그라운드로 시작. 실패해도 예외 없이 로그만(jarvis 본체는 계속)."""
    global _task
    if not config.IOT_ENABLED:
        print("[iot] 비활성(IOT_ENABLED=false)")
        return
    if not config.MQTT_HOST:
        print("[iot] MQTT_HOST 미설정 → IoT 건너뜀")
        return
    import asyncio
    _task = asyncio.create_task(_run())


async def _run() -> None:
    """연결 유지 루프 — 끊기면 재연결. aiomqtt Client 를 컨텍스트로 살려둔다."""
    global _client, _ready
    import asyncio
    import aiomqtt
    while True:
        try:
            async with aiomqtt.Client(
                hostname=config.MQTT_HOST, port=config.MQTT_PORT,
                username=config.MQTT_USER or None, password=config.MQTT_PASS or None,
            ) as client:
                _client, _ready = client, True
                print(f"[iot] MQTT 연결됨: {config.MQTT_HOST}:{config.MQTT_PORT}")
                async for _ in client.messages:
                    pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            _ready, _client = False, None
            print(f"[iot] MQTT 연결 실패/끊김, 5초 후 재시도: {e}")
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
    _ready, _client = False, None


async def scan(seconds: float = 8.0) -> list[str]:
    """짧게 구독해 관측된 토픽 목록을 모아 반환(실기기 토픽 파악용 디버그)."""
    if not config.MQTT_HOST:
        return []
    import asyncio
    import aiomqtt
    topics: set[str] = set()
    try:
        async with aiomqtt.Client(
            hostname=config.MQTT_HOST, port=config.MQTT_PORT,
            username=config.MQTT_USER or None, password=config.MQTT_PASS or None,
        ) as client:
            await client.subscribe("#")

            async def _collect():
                async for m in client.messages:
                    topics.add(str(m.topic))

            try:
                await asyncio.wait_for(_collect(), timeout=seconds)
            except asyncio.TimeoutError:
                pass
    except Exception as e:
        print(f"[iot] scan 실패: {e}")
    return sorted(topics)


async def close() -> None:
    global _task, _ready, _client
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except Exception:
            pass
    _task, _ready, _client = None, False, None
