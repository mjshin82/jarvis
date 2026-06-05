# 설정 기능 (미팅 번역/STT 백엔드) 설계

날짜: 2026-06-06

## 목표

웹 `+` 메뉴에 **⚙️ 설정** 항목 → 설정 팝업에서 (1) **미팅 번역** 딥시크(기본)/로컬, (2) **미팅 STT**
Deepgram(기본)/로컬 을 고른다. jarvis 가 `setting.yaml` 로 저장·적용. 콘솔은 직접 편집 없이
`/reload-settings` 로 재로드만.

## 확정 결정

- `setting.yaml` = 저장소 루트, **.gitignore**(사용자별).
- STT 기본값 `deepgram` 이지만 이번 작업은 **연동 없이 로컬로 동작**(로그) — Deepgram 연동은 다음 작업.
- 설정은 **다음 `/meet` 세션부터** 반영(세션 시작 시 읽음).

## 컴포넌트

### `settings.py` (신규, jarvis)

- 단일 진실원. 모듈 레벨 `_current` dict.
- `DEFAULTS = {"translate_backend": "deepseek", "stt_backend": "deepgram"}`.
- `ALLOWED = {"translate_backend": {"deepseek","local"}, "stt_backend": {"deepgram","local"}}`.
- `PATH` = 저장소 루트 `setting.yaml`(이 파일 기준 경로).
- `load()`: 파일 있으면 yaml 읽어 유효 키/값만 `_current` 갱신(기본값 위에 병합), 없으면 기본값으로
  파일 생성. 예외 시 기본값 유지 + 로그.
- `apply(updates: dict)`: `ALLOWED` 통과한 키/값만 `_current` 갱신 후 `save()`. 반환 `current()`.
- `get(key)` / `current()`(복사본) / `save()`.
- PyYAML 사용. `requirements.txt` 에 `pyyaml` 추가.

### 적용 — `live_translate.py` (다음 회의부터)

- `_setup_translator`: `use_remote` 조건을
  `settings.get("translate_backend") == "deepseek" and DEEPSEEK_API_KEY 유효` 로 변경.
- `MeetingSession.start` recorder 생성부: `stt = settings.get("stt_backend")`. `local` 이면 기존
  RealtimeSTT. `deepgram` 이면 로그("⚙️ Deepgram STT 는 다음 작업 — 현재 로컬 STT 사용") 후 동일하게
  로컬 recorder 생성(이번 작업 폴백). (분기점 확보, 실제 Deepgram 은 다음.)

### 제어 채널 페이로드 — `control_receiver.py` + `main.py`

- `control_receiver._handle_message`: `await self.on_command(kind)` → **`await self.on_command(msg)`**
  (전체 dict). (`no_receiver` 는 기존대로 로그.)
- `main._on_remote_command(msg)`: 첫 줄 `kind = msg.get("kind")`, 나머지 분기 그대로. 추가:
  - `"get_settings"`: `web_pub.emit("settings", json.dumps(settings.current()))`.
  - `"apply_settings"`: `settings.apply(msg.get("value") or {})` → 변경 후 스냅샷 재발행 + 로그.
- 기존 단위 테스트(`test_control_receiver`)를 dict 수신 형태로 갱신.

### jarvis→web 스냅샷 — `main.py` + `types.ts`

- 시작 시 `settings.load()` 후 `web_pub.emit("settings", json.dumps(settings.current()))` 1회
  (replay 버퍼에 들어가 늦게 붙는 owner 도 받음). 변경/`get_settings`/reload 시에도 발행.
- `web_pub` 를 `cmd_ctx["web_pub"]` 에 추가(/reload-settings 가 발행).
- `types.ts` `EventKind` 에 `"settings"` 추가. (DO `PUBLIC_KINDS` 미포함 → 공개 뷰어엔 안 감.)

### 웹 팝업 — `app.html`

- `#plus-menu` 에 `<button id="menu-settings">⚙️ 설정</button>`.
- `#settings-modal`(오버레이 + 카드): 두 라디오 그룹 — 번역(`딥시크`/`로컬`), STT(`Deepgram`/`로컬`),
  닫기 ✕.
- 열기: 메뉴 클릭 → 모달 표시 + `sendControl({kind:"get_settings"})`.
- `settings` 이벤트 수신 → `JSON.parse(ev.text)` → 라디오 체크 반영.
- 라디오 변경 시 → `sendControl({kind:"apply_settings", value:{translate_backend, stt_backend}})`
  (현재 두 값 모두 전송 — 자동 저장).

### `/reload-settings` 명령 — `commands.py`

- `@command("reload-settings", help="setting.yaml 재로드")`: `settings.load()` + `ctx["web_pub"]`
  있으면 스냅샷 발행 + 로그. **편집 명령 없음**(요청대로).

## 데이터 흐름

```
[+] → ⚙️ 설정 → 모달 + get_settings → /control → ControlReceiver → on_command(msg)
  → web_pub.emit("settings", json) → /subscribe(owner) → 라디오 반영
라디오 변경 → apply_settings(value) → settings.apply → setting.yaml 저장 + 스냅샷 재발행
/meet → MeetingSession.start → settings.get(...) 로 번역/STT 백엔드 결정
콘솔 /reload-settings → settings.load() → 스냅샷 발행
```

## 엣지 케이스

- **setting.yaml 없음**: load() 가 기본값으로 생성.
- **잘못된 값**: apply() 가 ALLOWED 로 필터(무시).
- **회의 중 변경**: 현재 세션엔 무영향(다음 세션부터). — 의도된 동작.
- **공개 뷰어**: settings 이벤트 안 받음(PUBLIC_KINDS 제외).
- **web_pub 미설정(RELAY 없음)**: 스냅샷 발행 무시, 로컬 동작 정상.
- **Deepgram 선택**: 저장되지만 런타임은 로컬(로그) — 다음 작업에서 연동.

## 테스트 전략

- **단위(jarvis):** `settings.py` — 기본값/load(없음→생성)/apply(유효·무효 필터)/저장·재로드 (tmp 경로).
  `control_receiver` — `on_command` 가 전체 dict 받음(get_settings/apply_settings/기존 kind).
- **웹:** `app.html` `node --check`.
- **수동 E2E:** 웹 `+`→설정 → "로컬" 선택 → setting.yaml 에 `translate_backend: local` 저장 →
  `/meet` 시 콘솔에 "local (...)" 번역 라벨. 콘솔 `/reload-settings` → 재로드 로그.

## 비범위

- Deepgram STT 실제 연동(다음 작업). 다른 설정 항목(미래). 실시간(회의 중) 적용.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `settings.py` (신규) | setting.yaml 로드/적용/저장 |
| `setting.yaml` (신규, gitignore) | 저장 파일(기본값) |
| `.gitignore` | `setting.yaml` 추가 |
| `requirements.txt` | `pyyaml` 추가 |
| `live_translate.py` | 번역/STT 백엔드를 settings 기준으로 |
| `control_receiver.py` | `on_command(msg)` 전체 dict |
| `main.py` | `_on_remote_command(msg)` + get/apply_settings + 시작 시 스냅샷 + cmd_ctx web_pub |
| `commands.py` | `/reload-settings` |
| `jarvis-web/src/types.ts` | `settings` kind |
| `jarvis-web/src/static/app.html` | ⚙️ 설정 메뉴 + 모달 + settings 이벤트 |
| `tests/` | settings.py, control_receiver 갱신 |
