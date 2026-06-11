# jarvis IR 가전 제어 (eMotion Pro / MQTT 직결) — 설계

- **날짜**: 2026-06-11
- **상태**: 승인됨 (구현 대기)
- **대상 기기**: LinknLink eMotion Pro (24GHz mmWave 재실센서 + 장거리 IR 블래스터, WiFi→MQTT)

## 배경 / 목표

음성으로 TV·에어컨 같은 IR 가전을 제어한다. eMotion Pro는 WiFi로 **MQTT 브로커(Mosquitto)에 직접** 붙어서, 학습된 IR 가상기기의 명령 토픽을 구독하는 구조다. 사용자 환경에는 **브로커만 있고 Home Assistant는 없다.** 따라서 jarvis는 또 하나의 MQTT 클라이언트로 붙어, eMotion이 구독하는 IR 명령 토픽에 직접 publish한다 (안 A: 직접 MQTT).

### 핵심 제약 / 미지수

LinknLink이 IR을 MQTT로 노출하는 **정확한 토픽 문자열·페이로드 포맷은 공개 문서에 없다.** "표준 MQTT 디스커버리로 HA에 자동 등록되고 IR 버튼이 엔티티로 동기화된다"까지만 확인됨. 실제 토픽은 **실기기에 붙어 한 번 떠봐야** 알 수 있다 → 빌드 1단계로 스캔을 먼저 한다.

매핑 방식은 **고정 맵(YAML)** 채택: 토픽을 한 번 스캔해 친근한 이름 → (토픽, 페이로드)를 수동 매핑. 토이 프로젝트에 가장 단순·투명. (대안인 자동 디스커버리 파싱은 코드량·포맷 의존이 커서 보류.)

맵은 **전용 `iot.yaml`** 에 둔다 (`setting.yaml` 아님). 이유: 기존 `settings.py`는 `ALLOWED` 화이트리스트로 스칼라 토글 키만 통과시키고 중첩 맵은 버리므로 IR 맵을 담을 수 없다. `iot.py`가 `iot.yaml`을 직접 로드한다.

## 아키텍처 / 데이터 흐름

```
음성 → STT → ┬─ fast-path intent ("에어컨 켜줘") ─┐
             └─ LLM 툴 콜 ("거실 26도로 약하게") ──┴→ IotController.send()
                                                      → aiomqtt publish(topic, payload)
                                                      → eMotion Pro 가 IR 송신 → TV/AC 반응
```

IR은 단방향(fire-and-forget)이라 초기 버전은 상태 피드백 없이 "전송함"까지만 보장한다. 재실센서/가전 상태 구독은 범위 밖(추후).

## 컴포넌트

### 1. `iot.py` — 모듈 레벨 함수 (music.py 패턴 차용)

기존 `music.py`가 `play_music()`/`stop_music()`를 모듈 함수로 노출하고 `llm.py`가 직접 import하는 패턴을 그대로 따른다. 내부에 단일 `aiomqtt` 클라이언트를 백그라운드 태스크로 유지한다.

**config 로드(동기)와 connect(비동기 네트워크)를 분리한다** — LLM 툴 스펙을 만들 때 가전 목록이 필요하므로, `LLM()` 생성 전에 `load_config()`(동기 yaml 파싱)가 끝나 있어야 한다. 네트워크 `connect()`는 그 후 비동기로.

- `load_config(path=None)` — `iot.yaml` 파싱해 `_appliances` 채움. 순수·동기. 파일 없으면 빈 맵
- `list_appliances() -> list[str]`, `commands_for(appliance) -> list[str]`, `resolve(name) -> str|None` (alias→키) — 순수, load_config 후 사용 가능
- `async connect()` — 브로커 접속 백그라운드 태스크 시작(재연결 루프 포함). **실패해도 예외 없이** 로그만, `available()`이 False 유지
- `available() -> bool`
- `async send(appliance, command, value=None) -> str` — alias로 가전 해석 → 맵에서 `(topic, payload)` 조회 → 페이로드 `{value}` 치환 → publish. 사용자용 결과 문자열 반환(play_music과 동형)
- `async scan(seconds) -> list[str]` — `#`(또는 `homeassistant/#`)를 잠깐 구독해 관측된 토픽 목록 수집·반환 (실기기 토픽 파악용 디버그)
- `async close()` — graceful 종료

### 2. `iot.yaml` 스키마 (맵은 여기, 비밀은 `.env`)

```yaml
appliances:
  aircon:
    aliases: ["에어컨", "에어콘"]
    commands:
      power:    { topic: ".../power",            payload: "ON" }
      set_temp: { topic: ".../temperature/set",  payload: "{value}" }
      mode:     { topic: ".../mode/set",         payload: "{value}" }   # cool/heat/fan
  tv:
    aliases: ["티비", "TV", "텔레비전"]
    commands:
      power:  { topic: ".../power", payload: "..." }
      vol_up: { topic: ".../volup", payload: "..." }
```

토픽 문자열은 빌드 1단계 스캔으로 실제 값을 채운다. 저장소엔 `iot.example.yaml`만 커밋하고 `iot.yaml`은 `.gitignore`. MQTT 자격증명(`MQTT_USER`/`MQTT_PASS`)은 yaml이 아니라 `.env`에 둔다. on/off 활성화는 `config.IOT_ENABLED`(.env).

### 3. `commands.py` — `/ir` 슬래시 커맨드

기존 `@command` 데코레이터 패턴을 따른다.

- `/ir scan` — 실기기 토픽을 떠서 출력 (YAML 채우기용)
- `/ir list` — 등록된 가전·명령 목록
- `/ir <가전> <명령> [값]` — 수동 전송 (디버그)

### 4. `llm.py` — LLM 툴 + fast-path

- `_TOOL_CONTROL_APPLIANCE` 함수 스펙: `appliance`(맵 기반 enum), `command`(string), `value?`(number)
- 핸들러 `_control_appliance()` → `await iot.send(...)`
- 기존 `SEARCH_ENABLED`/`MUSIC_ENABLED` 패턴대로 `config.IOT_ENABLED`일 때만 `self.tools`에 등록
- `appliance_intent.py` — `music_intent.py`와 동형의 fast-path 분류기. "에어컨 켜줘", "티비 꺼줘" 같은 명백한 명령은 LLM을 건너뛰고 바로 `iot.send()` 호출

### 5. `config.py` / `.env`

- 신규 키: `IOT_ENABLED`, `MQTT_HOST`, `MQTT_PORT`(기본 1883), `MQTT_USER`, `MQTT_PASS`
- `.env.example` 갱신

### 6. `main.py` 배선

- `IotController` 인스턴스화 → 시작 시 `connect()` → command ctx와 llm 툴 의존성에 DI (기존 `web_pub` 등과 동일 방식) → 종료 시 `close()`

### 7. 의존성

- `requirements.txt`에 `aiomqtt` 추가 (asyncio 네이티브, paho 래핑)

## 에러 처리

- **브로커 불통** → `available = False`, 툴/커맨드는 "IoT 비활성" 안내, jarvis 정상 구동
- **미등록 가전/명령** → 친절한 안내 (로그 + TTS)
- **publish 실패** → 로그 + "명령을 못 보냈어" 안내

## 테스트

- **단위**: YAML 맵 로딩, alias 해석, 페이로드 `{value}` 치환 — 순수 함수, 브로커 불필요
- **intent 분류기**: `music_intent` 테스트 패턴 차용
- **publish 검증**: mock MQTT 클라이언트를 주입해 올바른 topic/payload로 publish하는지 검증
- **수동**: `/ir scan` 후 `/ir aircon set_temp 26`을 실기기에 전송

## 빌드 순서 (리스크 먼저)

1. **스캔 스파이크** — 최소 `iot.py` + `/ir scan`으로 실기기 토픽 확보 → YAML 채움 (가장 큰 미지수 제거)
2. **코어 send 경로** + `/ir` 수동 커맨드 (콘솔로 검증)
3. **LLM 툴** + fast-path intent
4. **테스트 + 마무리**

## 범위 밖 (YAGNI)

- Home Assistant 경유 제어 (브로커 직결로 충분)
- 자동 디스커버리 파싱 (고정 맵으로 대체)
- 가전 상태 피드백 / 재실센서 활용 / 재실 기반 자동화
- IR 학습(teach) UI — 학습은 LinknLink 앱에서 수행
```
