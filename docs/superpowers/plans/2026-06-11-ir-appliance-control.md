# IR 가전 제어 (eMotion Pro / MQTT) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 음성으로 IR 가전(TV/에어컨)을 제어한다 — jarvis가 MQTT 클라이언트로 브로커에 붙어 eMotion Pro의 IR 명령 토픽에 직접 publish.

**Architecture:** 기존 `music.py`/`music_intent.py` 패턴을 그대로 따른다. `iot.py`가 모듈 레벨 함수(`send`/`scan`/`list_appliances`)를 노출하고 단일 `aiomqtt` 클라이언트를 백그라운드로 유지한다. 음성 → fast-path intent 또는 LLM 툴 콜 → `iot.send()` → publish. 가전→토픽 매핑은 전용 `iot.yaml`(고정 맵). config 로드(동기)와 connect(비동기)는 분리한다.

**Tech Stack:** Python 3.11 asyncio, `aiomqtt`(paho 래핑), `pyyaml`(기존 사용), `pytest`/`pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-06-11-ir-appliance-control-design.md`

---

## File Structure

- **Create** `iot.py` — MQTT 클라이언트 + 가전 맵 로딩 + send/scan (모듈 함수)
- **Create** `appliance_intent.py` — 순수 fast-path 분류기 (music_intent와 동형)
- **Create** `iot.example.yaml` — 가전 맵 템플릿 (저장소 커밋용)
- **Create** `tests/test_iot.py`, `tests/test_appliance_intent.py`
- **Modify** `config.py` — `IOT_ENABLED`, `MQTT_*`, `IOT_FILLER`, `IOT_CONFIG_PATH`
- **Modify** `requirements.txt` — `aiomqtt` 추가
- **Modify** `.env.example` — MQTT 키 추가
- **Modify** `.gitignore` — `iot.yaml` 무시
- **Modify** `commands.py` — `/ir` 슬래시 커맨드
- **Modify** `llm.py` — `_TOOL_CONTROL_APPLIANCE` 등록 + `_run_tool` + fast-path
- **Modify** `main.py` — `iot.load_config()`(LLM 생성 전) + `await iot.connect()` + cmd_ctx + 종료 시 close

---

## Task 1: 의존성 + config 키

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py:64-73` (음악 설정 근처에 IOT 블록 추가)
- Modify: `.env.example`
- Test: `tests/test_config_iot.py`

- [ ] **Step 1: 의존성 설치**

`requirements.txt` 끝에 한 줄 추가:

```
aiomqtt>=2.3.0
```

Run: `pip install "aiomqtt>=2.3.0" "pytest-asyncio>=0.23"`
Expected: 설치 성공 (pytest-asyncio는 비동기 테스트용)

- [ ] **Step 2: 실패하는 config 테스트 작성**

Create `tests/test_config_iot.py`:

```python
import config


def test_iot_defaults():
    # IOT_ENABLED 는 기본 비활성(브로커 없이도 jarvis 가 떠야 하므로)
    assert config.IOT_ENABLED is False
    assert config.MQTT_PORT == 1883
    assert config.MQTT_HOST == ""
    assert isinstance(config.IOT_FILLER, str) and config.IOT_FILLER
    assert config.IOT_CONFIG_PATH.endswith("iot.yaml")
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `pytest tests/test_config_iot.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'IOT_ENABLED'`

- [ ] **Step 4: config.py 에 IOT 블록 추가**

`config.py`의 음악 설정 블록(73번째 줄 `BROWSER_APP` 라인) 바로 아래에 추가:

```python
# --- IR 가전 제어 (eMotion Pro / MQTT) ---
# 음성으로 TV/에어컨 IR 가전 제어. 브로커에 붙어 가전의 명령 토픽에 publish.
IOT_ENABLED = os.getenv("IOT_ENABLED", "false").lower() in ("1", "true", "yes")
MQTT_HOST = os.getenv("MQTT_HOST", "")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
IOT_FILLER = "네, 처리할게요."   # 가전 명령 전송 동안 먼저 읽어줄 멘트
IOT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iot.yaml")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_config_iot.py -v`
Expected: PASS

- [ ] **Step 6: .env.example 갱신**

`.env.example`에 추가 (음악/검색 키 근처):

```
# IR 가전 제어 (eMotion Pro / MQTT). 브로커 IP/포트와 자격증명.
IOT_ENABLED=false
MQTT_HOST=
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
```

- [ ] **Step 7: 커밋**

```bash
git add requirements.txt config.py .env.example tests/test_config_iot.py
git commit -m "feat(iot): add MQTT/IoT config keys and aiomqtt dependency"
```

---

## Task 2: `iot.yaml` 로딩 + 순수 조회 함수

가전 맵 파싱과 alias 해석, 페이로드 템플릿팅 — 네트워크 없는 순수 로직.

**Files:**
- Create: `iot.py`
- Create: `iot.example.yaml`
- Modify: `.gitignore`
- Test: `tests/test_iot.py`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_iot.py`:

```python
import textwrap
import pytest
import iot


SAMPLE = textwrap.dedent("""
    appliances:
      aircon:
        aliases: ["에어컨", "에어콘"]
        commands:
          power:    { topic: "ir/aircon/power",       payload: "ON" }
          set_temp: { topic: "ir/aircon/temperature", payload: "{value}" }
      tv:
        aliases: ["티비", "TV"]
        commands:
          power:  { topic: "ir/tv/power", payload: "TOGGLE" }
""")


@pytest.fixture
def loaded(tmp_path):
    p = tmp_path / "iot.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    iot.load_config(str(p))
    return iot


def test_load_lists_appliances(loaded):
    assert set(loaded.list_appliances()) == {"aircon", "tv"}


def test_resolve_alias(loaded):
    assert loaded.resolve("에어컨") == "aircon"
    assert loaded.resolve("TV") == "tv"          # 대소문자/공백 무시
    assert loaded.resolve(" 티비 ") == "tv"
    assert loaded.resolve("aircon") == "aircon"  # 키 자체도 허용
    assert loaded.resolve("냉장고") is None


def test_commands_for(loaded):
    assert set(loaded.commands_for("aircon")) == {"power", "set_temp"}
    assert loaded.commands_for("없음") == []


def test_resolve_topic_payload_static(loaded):
    topic, payload = loaded.resolve_command("aircon", "power", None)
    assert topic == "ir/aircon/power"
    assert payload == "ON"


def test_resolve_topic_payload_templated(loaded):
    topic, payload = loaded.resolve_command("에어컨", "set_temp", 26)
    assert topic == "ir/aircon/temperature"
    assert payload == "26"


def test_resolve_command_unknown_returns_none(loaded):
    assert loaded.resolve_command("aircon", "없는명령", None) is None
    assert loaded.resolve_command("없는가전", "power", None) is None


def test_missing_file_is_empty(tmp_path):
    iot.load_config(str(tmp_path / "nope.yaml"))
    assert iot.list_appliances() == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_iot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot'`

- [ ] **Step 3: `iot.py` 순수 부분 구현**

Create `iot.py`:

```python
"""IR 가전 제어 — eMotion Pro(LinknLink) MQTT 직결.

music.py 패턴을 따라 모듈 레벨 함수로 노출한다(llm.py 가 직접 import).
가전→토픽 매핑은 iot.yaml(고정 맵). config 로드(동기)와 connect(비동기 네트워크)를
분리한다 — LLM 툴 스펙 생성 시 가전 목록이 필요하므로 LLM() 전에 load_config() 가
끝나 있어야 한다.

eMotion Pro 의 실제 토픽/페이로드 포맷은 공개 문서에 없다. `/ir scan` 으로 실기기에서
관측한 토픽을 iot.yaml 에 채워 넣는다.
"""
import config

_appliances: dict = {}   # 키 → {"aliases": [...], "commands": {name: {topic, payload}}}


def load_config(path: str = None) -> None:
    """iot.yaml 파싱 → _appliances. 순수·동기. 파일 없으면 빈 맵."""
    global _appliances
    import os
    import yaml
    p = path or config.IOT_CONFIG_PATH
    _appliances = {}
    if not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _appliances = data.get("appliances", {}) or {}
    except Exception as e:
        print(f"[iot] iot.yaml 로드 실패: {e}")
        _appliances = {}


def list_appliances() -> list[str]:
    return list(_appliances.keys())


def commands_for(appliance: str) -> list[str]:
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
    topic = spec["topic"]
    payload = str(spec.get("payload", ""))
    payload = payload.replace("{value}", "" if value is None else str(value))
    return topic, payload
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_iot.py -v`
Expected: PASS (8개)

- [ ] **Step 5: `iot.example.yaml` + .gitignore**

Create `iot.example.yaml`:

```yaml
# IR 가전 → MQTT 명령 매핑. 복사해서 iot.yaml 로 쓰고 실제 토픽을 채운다.
# 토픽 문자열은 `/ir scan` 으로 실기기에서 관측한 값으로 교체할 것.
# payload 의 {value} 는 음성에서 추출한 값(예: 온도)으로 치환된다.
appliances:
  aircon:
    aliases: ["에어컨", "에어콘"]
    commands:
      power:    { topic: "REPLACE/aircon/power",       payload: "ON" }
      set_temp: { topic: "REPLACE/aircon/temperature", payload: "{value}" }
      mode:     { topic: "REPLACE/aircon/mode",        payload: "{value}" }   # cool/heat/fan
  tv:
    aliases: ["티비", "TV", "텔레비전"]
    commands:
      power:  { topic: "REPLACE/tv/power",  payload: "TOGGLE" }
      vol_up: { topic: "REPLACE/tv/volup",  payload: "PRESS" }
```

`.gitignore`에 추가:

```
iot.yaml
```

- [ ] **Step 6: 커밋**

```bash
git add iot.py iot.example.yaml .gitignore tests/test_iot.py
git commit -m "feat(iot): load iot.yaml appliance map + pure topic/payload resolution"
```

---

## Task 3: MQTT 연결 + send/scan

브로커 연결을 백그라운드로 유지하고 publish/scan을 구현한다. send의 토픽/페이로드 해석은 Task 2에서 끝났으므로, 여기선 publish 호출만 검증한다(가짜 클라이언트 주입).

**Files:**
- Modify: `iot.py`
- Test: `tests/test_iot.py` (이어서)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_iot.py` 끝에 추가:

```python
class _FakeClient:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload=None):
        self.published.append((topic, payload))


@pytest.mark.asyncio
async def test_send_publishes_resolved(loaded, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(iot, "_client", fake)
    monkeypatch.setattr(iot, "_ready", True)

    msg = await iot.send("에어컨", "set_temp", 26)

    assert fake.published == [("ir/aircon/temperature", "26")]
    assert "26" in msg or "에어컨" in msg   # 사용자용 결과 멘트


@pytest.mark.asyncio
async def test_send_unknown_command_no_publish(loaded, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(iot, "_client", fake)
    monkeypatch.setattr(iot, "_ready", True)

    msg = await iot.send("에어컨", "없는명령", None)

    assert fake.published == []
    assert "모르" in msg or "없" in msg     # 안내 멘트


@pytest.mark.asyncio
async def test_send_when_unavailable(loaded, monkeypatch):
    monkeypatch.setattr(iot, "_client", None)
    monkeypatch.setattr(iot, "_ready", False)

    msg = await iot.send("에어컨", "power", None)

    assert "비활성" in msg or "연결" in msg
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_iot.py -k send -v`
Expected: FAIL — `AttributeError: module 'iot' has no attribute 'send'`

- [ ] **Step 3: iot.py 에 연결/전송 추가**

`iot.py` 상단 `_appliances` 선언 아래에 상태 변수 추가:

```python
_client = None        # 살아있는 aiomqtt.Client (없으면 None)
_ready = False        # 연결 준비 완료 여부
_task = None          # 백그라운드 연결 유지 태스크
```

`iot.py` 끝에 추가:

```python
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
                # 연결을 살려두기 위해 들어오는 메시지를 소비(없으면 그냥 대기)
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_iot.py -v`
Expected: PASS (11개)

- [ ] **Step 5: 커밋**

```bash
git add iot.py tests/test_iot.py
git commit -m "feat(iot): MQTT connect/reconnect loop + send/scan"
```

---

## Task 4: fast-path 의도 분류기

"에어컨 켜줘", "에어컨 26도" 같은 명백한 발화를 LLM 없이 분기. music_intent와 동형의 순수 함수. 동적 가전 alias를 인자로 받아 테스트 가능하게 한다.

**Files:**
- Create: `appliance_intent.py`
- Test: `tests/test_appliance_intent.py`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `tests/test_appliance_intent.py`:

```python
import appliance_intent as ai

ALIASES = {"aircon": ["에어컨", "에어콘"], "tv": ["티비", "TV"]}


def test_power_on():
    assert ai.classify("에어컨 켜줘", ALIASES) == ("aircon", "power", None)
    assert ai.classify("티비 켜줘", ALIASES) == ("tv", "power", None)


def test_power_off_maps_to_power():
    # 단일 power 토글 명령만 있는 가전: 꺼줘도 power 로
    assert ai.classify("에어컨 꺼줘", ALIASES) == ("aircon", "power", None)


def test_set_temp():
    assert ai.classify("에어컨 26도", ALIASES) == ("aircon", "set_temp", 26)
    assert ai.classify("에어컨 24도로 해줘", ALIASES) == ("aircon", "set_temp", 24)


def test_no_appliance_returns_none():
    assert ai.classify("오늘 날씨 어때", ALIASES) is None


def test_unknown_alias_returns_none():
    assert ai.classify("냉장고 켜줘", ALIASES) is None


def test_long_utterance_deferred_to_llm():
    long = "에어컨 좀 켜고 싶은데 지금 너무 더워서 그런데 혹시 26도 정도로 맞춰줄 수 있을까"
    assert ai.classify(long, ALIASES) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_appliance_intent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'appliance_intent'`

- [ ] **Step 3: 구현**

Create `appliance_intent.py`:

```python
"""Fast-path 의도 분류기(IR 가전) — music_intent 와 동형.

명백한 발화만 (가전, 명령, 값) 으로 분기하고, 모호하면 None 을 돌려 LLM 흐름에 맡긴다.
가전 alias 는 호출자가 iot.yaml 에서 읽어 넘긴다(테스트 가능하도록 인자화).
보수적으로 매칭한다(false positive 가 거짓 동작을 부르므로).
"""
import re

_POWER = re.compile(r"(켜줘|켜|꺼줘|꺼|틀어줘|틀어|꺼주라|켜주라)\s*\.?\s*$")
_TEMP = re.compile(r"(\d{1,2})\s*도")


def _find_appliance(text: str, aliases: dict) -> str | None:
    """발화에 등장하는 가전 키. 가장 먼저 매칭되는 것."""
    for key, names in aliases.items():
        cands = [key] + list(names or [])
        for c in cands:
            if str(c).lower() in text.lower():
                return key
    return None


def classify(text: str, aliases: dict):
    """(appliance_key, command, value) 또는 None."""
    if not text:
        return None
    t = text.strip()
    if len(t) > 25:          # 길면 의도가 섞임 → LLM
        return None
    appliance = _find_appliance(t, aliases)
    if appliance is None:
        return None
    # 온도 지정: "에어컨 26도"
    m = _TEMP.search(t)
    if m:
        return (appliance, "set_temp", int(m.group(1)))
    # 전원: "켜줘/꺼줘"
    if _POWER.search(t):
        return (appliance, "power", None)
    return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_appliance_intent.py -v`
Expected: PASS (6개)

- [ ] **Step 5: 커밋**

```bash
git add appliance_intent.py tests/test_appliance_intent.py
git commit -m "feat(iot): appliance fast-path intent classifier"
```

---

## Task 5: `/ir` 슬래시 커맨드

스캔(토픽 파악)·목록·수동 전송. 1단계 스캔과 디버그의 진입점.

**Files:**
- Modify: `commands.py` (파일 끝에 추가)

- [ ] **Step 1: `/ir` 커맨드 추가**

`commands.py` 끝에 추가:

```python
@command("ir", help="IR 가전 제어 (스캔/목록/전송)",
         usage="scan | list | <가전> <명령> [값]")
async def _ir(args: str, ctx: dict):
    import iot
    parts = args.split()
    if not parts:
        ctx["log"]("사용법: /ir scan | /ir list | /ir <가전> <명령> [값]")
        return
    sub = parts[0].lower()

    if sub == "scan":
        ctx["log"]("🔎 MQTT 토픽 스캔 중(8초)…")
        topics = await iot.scan(8.0)
        if not topics:
            ctx["log"]("관측된 토픽 없음 (MQTT_HOST/권한 확인).")
            return
        ctx["log"](f"관측된 토픽 {len(topics)}개:")
        for t in topics:
            ctx["log"](f"  {t}")
        return

    if sub == "list":
        apps = iot.list_appliances()
        if not apps:
            ctx["log"]("등록된 가전 없음 (iot.yaml 확인).")
            return
        for a in apps:
            ctx["log"](f"  {a}: {', '.join(iot.commands_for(a))}")
        return

    # /ir <가전> <명령> [값]
    if len(parts) < 2:
        ctx["log"]("사용법: /ir <가전> <명령> [값]")
        return
    appliance, command = parts[0], parts[1]
    value = parts[2] if len(parts) > 2 else None
    msg = await iot.send(appliance, command, value)
    ctx["log"](f"📡 {msg}")
```

- [ ] **Step 2: 등록 확인 (수동)**

Run: `python -c "import commands; assert 'ir' in commands._REGISTRY; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add commands.py
git commit -m "feat(iot): /ir slash command (scan/list/send)"
```

---

## Task 6: LLM 툴 + fast-path 배선

LLM이 `control_appliance` 툴을 호출하고, 명백한 발화는 fast-path로 우회한다.

**Files:**
- Modify: `llm.py` (import, 툴 정의, `__init__`, `_fast_path`, `_run_tool`, `respond`)

- [ ] **Step 1: import 추가**

`llm.py:22` `import music_intent` 아래에 추가:

```python
import iot
import appliance_intent
```

- [ ] **Step 2: 툴 정의 추가**

`llm.py`의 `_TOOL_STOP_MUSIC`(66번째 줄) 정의 아래에 추가:

```python
_TOOL_CONTROL_APPLIANCE = {
    "type": "function",
    "function": {
        "name": "control_appliance",
        "description": "TV·에어컨 같은 IR 가전을 제어한다. 사용자가 가전을 켜고/끄거나 "
                       "에어컨 온도·모드를 바꿔달라고 할 때 사용.",
        "parameters": {
            "type": "object",
            "properties": {
                "appliance": {"type": "string", "description": "가전 이름(예: 에어컨, 티비)"},
                "command": {"type": "string", "description": "명령(예: power, set_temp, mode, vol_up)"},
                "value": {"type": "string", "description": "값(예: 온도 26, 모드 cool). 없으면 생략"},
            },
            "required": ["appliance", "command"],
        },
    },
}
```

- [ ] **Step 3: `__init__` 에서 툴 등록**

`llm.py`의 `if config.MUSIC_ENABLED:` 블록(95-98번째 줄) 바로 아래에 추가:

```python
            if config.IOT_ENABLED:
                self.tools.append(_TOOL_CONTROL_APPLIANCE)
                apps = ", ".join(iot.list_appliances()) or "(없음)"
                base += (f"\nTV/에어컨 등 IR 가전 제어는 control_appliance 도구를 쓴다. "
                         f"등록된 가전: {apps}.")
```

- [ ] **Step 4: `_run_tool` 에 핸들러 추가**

`llm.py`의 `_run_tool` 안 `if name == "stop_music":` 블록(166번째 줄) 아래에 추가:

```python
        if name == "control_appliance":
            args2 = args  # 이미 파싱됨
            return await iot.send(
                args2.get("appliance", ""), args2.get("command", ""), args2.get("value"))
```

- [ ] **Step 5: fast-path 분기 추가 (respond)**

`llm.py`의 `respond` 안, 음악 fast-path 블록(280-285번째 줄) 바로 아래에 추가:

```python
        # Fast-path: 명백한 가전 명령은 LLM 없이 곧장 실행
        if self.use_tools and config.IOT_ENABLED:
            app_intent = appliance_intent.classify(user_text, _iot_aliases())
            if app_intent:
                appliance, command, value = app_intent
                self.history.append({"role": "user", "content": user_text})
                yield config.IOT_FILLER
                result = await iot.send(appliance, command, value)
                self.history.append({"role": "assistant", "content": config.IOT_FILLER})
                if result:
                    yield result
                return
```

- [ ] **Step 6: alias 헬퍼 추가**

`llm.py` 모듈 하단(클래스 밖, `_split_sentences` 근처)에 추가:

```python
def _iot_aliases() -> dict:
    """iot.yaml 의 가전→alias 맵(appliance_intent 입력용)."""
    return {a: iot.commands_for_aliases(a) for a in iot.list_appliances()}
```

그리고 `iot.py`에 보조 함수 추가(`commands_for` 아래):

```python
def commands_for_aliases(appliance: str) -> list[str]:
    """가전의 alias 목록(intent 분류기 입력용)."""
    spec = _appliances.get(appliance) or {}
    return list(spec.get("aliases") or [])
```

- [ ] **Step 7: 회귀 테스트 + import 확인**

Run: `pytest tests/test_iot.py tests/test_appliance_intent.py -v && python -c "import llm; print('import ok')"`
Expected: 기존 테스트 PASS + `import ok` (mock 모드에선 IOT 툴 미등록이라 부작용 없음)

- [ ] **Step 8: 커밋**

```bash
git add llm.py iot.py
git commit -m "feat(iot): wire control_appliance LLM tool + fast-path intent"
```

---

## Task 7: `main.py` 배선

config 로드(LLM 생성 전) → connect(비동기) → cmd_ctx → 종료 시 close.

**Files:**
- Modify: `main.py:48-58` (load_config + connect), `main.py:148-157` (cmd_ctx), `main.py:533-536` (close)

- [ ] **Step 1: import + load_config + connect**

`main.py`의 `from player import Player`(43번째 줄) 근처 import 블록에 추가:

```python
import iot
```

`main.py:51` `llm = LLM()` **바로 위에** 추가(LLM 이 가전 목록을 읽어 툴 스펙을 만들므로 먼저 로드):

```python
    iot.load_config()
```

`main.py:58` `await llm.warmup()` 아래에 추가:

```python
    await iot.connect()
```

- [ ] **Step 2: cmd_ctx 에 노출(선택적 디버그용)**

`main.py:148` `cmd_ctx = {` 딕셔너리에 한 줄 추가:

```python
        "iot": iot,
```

- [ ] **Step 3: 종료 시 close**

`main.py`의 `try: await backend.close()`(533번째 줄) **바로 위에** 추가:

```python
        try:
            await iot.close()
        except Exception:
            pass
```

- [ ] **Step 4: 부팅 스모크 테스트**

Run: `python -c "import main; print('main import ok')"`
Expected: `main import ok` (구문/임포트 오류 없음)

브로커 없이 기본 설정(IOT_ENABLED=false)으로 jarvis가 평소처럼 떠야 한다. 수동 확인:
Run: `./run.sh` → 콘솔이 뜨고 `[iot] 비활성(IOT_ENABLED=false)` 로그 확인 후 `/bye`
Expected: 평소처럼 부팅·종료, IoT 관련 크래시 없음

- [ ] **Step 5: 커밋**

```bash
git add main.py
git commit -m "feat(iot): wire iot load_config/connect/close into main lifecycle"
```

---

## Task 8: 실기기 스캔 + iot.yaml 채우기 (수동, 빌드 1단계의 실제 수행)

코드가 다 들어갔으니, 실기기로 토픽을 떠서 `iot.yaml`을 완성한다. **이 태스크는 실제 eMotion Pro + 브로커가 있어야 수행 가능.**

- [ ] **Step 1: .env 설정**

`.env`에 실제 브로커 정보 입력:

```
IOT_ENABLED=true
MQTT_HOST=<브로커 IP>
MQTT_PORT=1883
MQTT_USER=<있으면>
MQTT_PASS=<있으면>
```

LinknLink 앱에서 eMotion Pro → MQTT Connection에 동일 브로커 IP/포트/계정 입력해 기기를 브로커에 붙인다. IR 가전(TV/에어컨)은 LinknLink 앱에서 미리 학습/등록한다.

- [ ] **Step 2: 토픽 스캔**

`cp iot.example.yaml iot.yaml` 후 jarvis 실행 → 콘솔에서:

```
/ir scan
```

그동안 LinknLink 앱에서 각 가전 버튼(에어컨 전원, 온도, TV 전원 등)을 한 번씩 눌러 해당 명령 토픽이 트래픽에 뜨게 한다. 출력된 토픽 목록에서 IR 가전 명령 토픽을 식별한다.

(보조: `mosquitto_sub -h <IP> -v -t '#'` 로도 동일하게 관측 가능.)

- [ ] **Step 3: iot.yaml 채우기**

관측한 토픽/페이로드로 `iot.yaml`의 `REPLACE/...` 자리를 교체한다. HA MQTT 디스커버리 구조라면 `homeassistant/climate/<id>/.../config` 메시지의 `*_command_topic` 필드가 에어컨 온도/모드 토픽을, `button`/`switch` 엔티티의 `command_topic`+`payload_*`가 TV 버튼을 알려준다.

- [ ] **Step 4: 검증**

```
/ir list
/ir aircon power
/ir aircon set_temp 26
/ir tv power
```

Expected: 각 명령에서 실제 가전이 반응(IR 송신됨). 반응 없으면 토픽/페이로드를 재확인.

- [ ] **Step 5: 음성 종단 확인**

"Hey Jarvis" → "에어컨 켜줘" / "에어컨 26도" / "티비 켜줘"
Expected: fast-path로 즉시 동작. 복합 발화("좀 시원하게 26도로 맞춰줘")는 LLM 툴 콜로 동작.

- [ ] **Step 6: 커밋(맵 템플릿 변화가 있었다면)**

```bash
# iot.yaml 은 .gitignore 됨. 예시 구조가 바뀌었으면 example 만 갱신.
git add iot.example.yaml
git commit -m "docs(iot): refine iot.example.yaml after real-device scan"
```

---

## Self-Review 결과 (작성자 점검)

- **Spec 커버리지:** 안 A(직접 MQTT)=Task 3/7, 고정 맵 YAML=Task 2, fast-path=Task 4/6, LLM 툴=Task 6, `/ir` 스캔·디버그=Task 5, config/.env=Task 1, main 배선=Task 7, 에러 처리(브로커 불통 graceful)=Task 3(`available()`/`send`)·Task 7(connect 예외 격리), 테스트=Task 2/3/4. 스캔 선행=Task 8. 누락 없음.
- **범위 밖(스펙대로 제외):** HA 경유, 자동 디스커버리 파싱, 상태 피드백/재실 자동화, IR 학습 UI — 어느 태스크에도 없음(의도적).
- **타입 일관성:** `iot.send(appliance, command, value)` 시그니처가 llm·commands·테스트에서 동일. `resolve_command`는 `(topic,payload)|None`, `classify`는 `(key,command,value)|None`로 호출부와 일치. `_iot_aliases()`가 쓰는 `iot.commands_for_aliases()`는 Task 6 Step 6에서 정의됨.
- **플레이스홀더:** `iot.example.yaml`/스펙의 `REPLACE`·`.../`는 Task 8에서 실측으로 채우는 의도적 값(코드 플레이스홀더 아님).
```
