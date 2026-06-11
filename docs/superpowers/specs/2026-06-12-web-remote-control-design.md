# jarvis 웹 리모컨 (IR 가전 제어 UI) — 설계

- **날짜**: 2026-06-12
- **상태**: 승인됨 (구현 대기)
- **선행**: [IR 가전 제어](2026-06-11-ir-appliance-control-design.md) — jarvis 가 MQTT 로 eMotion Pro IR 가전(TV/에어컨) 제어. 실기기 검증 완료.

## 배경 / 목표

지금은 음성으로만 IR 가전을 제어할 수 있다. jarvis 웹(app.html)의 우측 하단 `+` 메뉴에 **"리모컨"** 항목을 추가해, 탭 한 번으로 TV·에어컨을 버튼으로 직접 제어한다. 웹 버튼이 기존 control 채널로 jarvis 에 명령을 보내면 jarvis 가 `iot.send()` 로 IR 을 쏜다.

## UX 결정 (비주얼 브레인스토밍 결과)

- **표현 방식**: 바텀 시트(모달) — 회의 시작 폼(`#meeting-form`)과 같은 패턴. 헤더 탭 `[📺 TV][❄️ 에어컨]` 으로 기기 전환, 바깥 탭/✕ 으로 닫힘.
- **TV**: 핵심만 — 전원·음소거·입력 + 볼륨 로커(±) + 채널 로커(±).
- **에어컨**: 서모스탯 — 큰 온도 숫자 양옆 −/＋ + 전원 토글 + 모드 칩(냉방/난방/제습/송풍) + 바람 칩(자동/약/중/강), 선택 칩 하이라이트.

## 아키텍처 / 데이터 흐름

```
리모컨 시트 버튼 클릭
 → sendControl({kind:"ir_command", appliance, command, value?})   (control WS, 기존 채널)
 → DO 가 web→jarvis 로 중계
 → jarvis control_receiver._handle_message → on_command(msg)
 → main.py _on_remote_command: elif kind=="ir_command" → await iot.send(appliance, command, value)
 → MQTT publish → eMotion Pro → IR → TV/에어컨
```

IR 은 단방향(fire-and-forget)·상태 미수신. 웹은 ack 를 기다리지 않고, AC 의 온도/전원/모드/바람은 **"마지막에 보낸 값"** 을 로컬 상태로 표시한다(낙관적).

## 컴포넌트

### 1. `jarvis-web/src/static/app.html` — 작업 대부분

- **`#plus-menu`** 에 `🎛️ 리모컨` 버튼 추가 (회의 버튼 옆).
- **`#remote-sheet`** 바텀 시트 신설 (`#meeting-form` 스타일 차용: `position:fixed; inset:0; background:#0008` + 중앙/하단 sheet). 헤더: 탭 `[📺 TV][❄️ 에어컨]` + ✕. 본문: 활성 탭에 따라 TV 패널/AC 패널 토글.
- **TV 패널**: 버튼 클릭 → `sendControl({kind:"ir_command", appliance:"tv", command})`
  - 전원=`power`, 음소거=`mute`, 입력=`tv_av`
  - 볼륨 로커: ＋=`volume_up`, －=`volume_down`
  - 채널 로커: ＋=`channel_up`, －=`channel_down`
- **AC 패널(서모스탯)**: 버튼 클릭 → `sendControl({kind:"ir_command", appliance:"aircon", command, value?})`
  - 온도 표시(로컬 상태, 기본 24, **16–32 클램프**). ＋/－ → 로컬값 ±1 후 `command:"set_temp", value:<새 온도>` 전송.
  - 전원 토글(로컬 on/off 상태) → `on` 또는 `off`. 모드 칩을 누르면 payload 특성상 자동으로 전원 ON 이 되므로 전원 상태도 ON 으로 갱신.
  - 모드 칩: 냉방=`cool`, 난방=`heat`, 제습=`dry`, 송풍=`fan_only`. 누른 칩 하이라이트(로컬 상태).
  - 바람 칩: 자동=`fan_auto`, 약=`fan_low`, 중=`fan_medium`, 강=`fan_high`. 누른 칩 하이라이트.
- **로컬 상태 영속**: AC 의 `{temp, power, mode, fan}` 를 localStorage 에 저장해 시트 재오픈 시 마지막 상태를 보여줌(키 예: `jarvis_ac_state`). 실제 기기 상태와 다를 수 있음(허용).
- **비활성/피드백**: `body.server-down` 일 때 리모컨 버튼 비활성(기존 셀렉터에 추가). 버튼 누르면 짧은 시각 피드백(눌림 플래시). 별도 토스트는 v1 에서 생략(YAGNI).

### 2. `main.py` — `_on_remote_command` 분기 추가

기존 `kind` 분기 체인(meeting/mic/listen/settings)에 한 분기 추가:
```python
elif kind == "ir_command":
    await iot.send(msg.get("appliance", ""), msg.get("command", ""), msg.get("value"))
```
`iot` 는 이미 import·connect 됨(선행 작업). `iot.send` 는 가전·명령을 iot.yaml 로 해석해 publish(없으면 무시+반환 문자열).

### 3. `jarvis-web/src/index.ts` / `meeting_do.ts` — 변경 없음(확인 필요)

control 채널은 이미 web→jarvis 임의 JSON 을 중계함(mic/meeting/listen 과 동일 경로). `ir_command` 도 그대로 통과. **플랜 1단계에서 DO 가 control 메시지를 종류 무관하게 중계하는지 코드로 확인.**

### 4. `iot.py` — 변경 없음

## 명령 매핑 (전부 현재 iot.yaml 에 존재)

- **TV**: `power, mute, tv_av, volume_up, volume_down, channel_up, channel_down`
- **에어컨**: `on, off, set_temp(value), cool, heat, dry, fan_only, fan_auto, fan_low, fan_medium, fan_high`

## 에러 처리

- **서버 다운**: 리모컨 버튼 비활성(`body.server-down`).
- **미등록 가전/명령**: jarvis `iot.send` 가 무시하고 로그(`'… 명령을 모르겠어요'`). 웹은 낙관적 — 사용자에게 별도 에러 없음(v1).
- **AC 상태 드리프트**: IR 단방향이라 로컬 상태가 실제와 어긋날 수 있음. 재설정으로 해결, localStorage 로 마지막 상태 유지.

## 테스트

- **jarvis 단위**: `_on_remote_command` 의 `ir_command` 분기가 `iot.send(appliance, command, value)` 를 올바른 인자로 호출하는지(가짜 `iot.send` 주입). 가능하면 `_on_remote_command` 를 테스트 가능한 형태로 노출/추출.
- **웹 수동**: 정적 HTML/JS 라 자동 테스트 없음. `wrangler dev`(로컬) + `./run.sh --local` 로 실기기 종단 검증 — 리모컨 시트의 TV/AC 버튼이 실제 TV·에어컨을 동작시키는지.

## 빌드 순서

1. **jarvis 측** — `_on_remote_command` 에 `ir_command` 분기 + 단위테스트(+ DO 중계 확인).
2. **웹 UI** — `#remote-sheet` 마크업/CSS + plus-menu 버튼 + 탭 전환 + TV/AC 패널 + `sendControl` 배선 + AC 로컬 상태/localStorage.
3. **수동 종단 검증** — 웹 버튼 → 실기기 반응.

## 범위 밖 (YAGNI)

- 기기 상태 양방향 동기화(IR 단방향이라 불가).
- iot.yaml 기반 동적 UI 생성(가전·버튼을 jarvis 가 웹에 푸시) — 지금은 TV/에어컨 고정 큐레이션. 가전이 늘면 그때 동적화 고려.
- 전송 결과 토스트/ack, 가전 추가/삭제 UI, IR 학습 UI(앱에서 수행).
