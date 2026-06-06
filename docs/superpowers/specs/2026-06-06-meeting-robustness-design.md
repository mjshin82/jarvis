# 미팅 견고성(로딩·재시작 복구·서버다운 표시·재배포 일관성) 설계

날짜: 2026-06-06

## 목표
- **F1**: 미팅 시작 시 진입까지 2~3초 걸리는 동안 웹 UI 를 전체 로딩 오버레이로 차단.
- **F2**: jarvis 재시작 시 마지막 지속 모드(회의/번역)를 로컬 저장값으로 자동 복구.
- **F3**: jarvis(서버) 다운 시 웹에 표시 + 음성버튼 비활성.
- **F4**: 웹 재배포(재접속) 시 회의 화면/상태 일관성 보장.

성격: 견고성 기능 추가. 기본 동작 보존(저장값 없으면 idle).

## 현재 사실(탐색 결과)
- `web_pub`(RelayClient publisher)은 jarvis 부팅 시 상시 연결, 연결마다 `hello`(meta) 송신.
  hello 는 replay 버퍼에 적재 → 늦은 owner 도 받음.
- jarvis 다운 = publisher 끊김 → DO `attachPublisher` 의 onClose 가 `publisher_disconnected` 브로드캐스트.
- `navigate` 는 의도적으로 replay 버퍼 미적재(과거 navigate 가 새 viewer 를 엉뚱하게 이동시키지 않도록).
  → 웹 재접속 시 자막은 replay 되나 **회의 뷰로 복원 안 됨**(F4 버그).
- DO 는 `lastMicSource` 를 owner 재접속 시 1회 재전송하는 패턴 보유 → "현재 뷰"도 동일 패턴으로 동기화 가능.

## 비범위 (YAGNI)
- 회의 meta(상대 이름 등) 영속화 — 현재 meta 는 config/기본값 기반이라 복구 시 재구성으로 충분.
- 브라우저 마이크 권한 영구 회수(불가) — 서버다운 시 웹이 캡처를 stop 할 뿐.
- DO 자체의 영속 스토리지 — `currentView`/`lastMicSource` 는 인메모리(연결 유지 동안). 충분.

---

## F1 — 미팅 시작 로딩 오버레이 (web `app.html`)

- 신규 `#meeting-loading` 전체화면 오버레이: `position:fixed; inset:0; background:var(--bg);
  z-index:60; display:flex(center)`, 스피너 + "회의 준비 중…". 기본 `.hidden`.
- `+메뉴 → 미팅`(`menu-meet`) 클릭 핸들러: 기존 `sendControl(meeting_start)` 직전/직후에
  오버레이 표시.
- **숨김**: `handle()` 의 `navigate` 케이스에서 target=="meeting" 이면 오버레이 숨김(회의 진입 완료).
- **안전 타임아웃**: 오버레이 표시 시 ~10초 타이머 → 아직 떠 있으면 숨김 + 간단 안내(`alert` 또는
  info). navigate 도착 시 타이머 취소.
- 음성 명령으로 회의 진입하는 경로는 오버레이 미표시(+메뉴 경로 전용) — navigate 가 와도
  "표시된 적 없으면" 숨김은 무해(no-op).

---

## F2 — jarvis 마지막 모드 복구 (Python)

### 신규 `runtime_state.py`
```python
# runtime_state.py
"""jarvis 런타임 상태 영속 — 재시작 시 마지막 지속 모드(회의/번역) 복구용.
setting.yaml(사용자 설정)과 별개. gitignore 된 .jarvis_state.json."""
import json, os
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".jarvis_state.json")
_ALLOWED = {"idle", "meeting", "translate"}
_last = None   # 중복 기록 방지

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
```
- `.gitignore` 에 `.jarvis_state.json` 추가.

### 컨트롤러 (conversation.py)
- 생성자 인자 `persist_mode=lambda m: None` 추가(저장 콜백 주입).
- 전이 시 호출:
  - `_set_idle`: `self.persist_mode("idle")`.
  - `_to_listening`(CONVERSING): `self.persist_mode("idle")` (대화는 복구 대상 아님 → idle 로).
  - `_begin_meeting`(MEETING live 성공 후): `self.persist_mode("meeting")`.
  - `start_translate`(TRANSLATE 진입): `self.persist_mode("translate")`.

### main.py 부팅 복구
- recognizer/control_rx start 직후, **초기 `await controller._set_idle()` 를 복구 분기로 교체**
  (먼저 idle 로 persist 하면 저장값을 덮어쓰므로 순서 중요):
  ```python
  _restore = runtime_state.load_mode()
  if _restore == "meeting":
      console.log("🎤 이전 회의 모드를 복구합니다.")
      await controller.start_meeting()
  elif _restore == "translate":
      console.log("🌐 이전 번역 모드를 복구합니다.")
      await controller.start_translate(None)
  else:
      await controller._set_idle()
  ```
- `persist_mode=runtime_state.save_mode` 를 컨트롤러 생성자에 주입.
- 복구 실패(예: Gladia/STT 미가동)는 기존 `_begin_meeting` try/except 가 idle 폴백.

---

## F3 — 서버다운 표시 + 음성버튼 비활성 (web + DO)

### DO (meeting_do.ts)
- `attachViewer`(owner) 에서 replay 후: `if (!this.publisher) this.safeSend(ws, this.buildEvent({kind:"publisher_disconnected"}));`
  → 웹이 접속 직후 서버다운을 즉시 인지(현재는 publisher 끊긴 시점에만 알림).

### web (app.html)
- `let serverUp = true;` (낙관적 시작 — DO 가 접속 직후 down 이면 교정).
- `handle()`:
  - `hello`: `serverUp = true; setServerUp(true);` (jarvis 살아있음 — 연결마다·replay 로 도착).
  - `publisher_disconnected` / `end`: `serverUp = false; setServerUp(false);`.
- `setServerUp(up)`:
  - up: `#conn` 초록 "● live"; 음성버튼 활성(`$("voice-toggle").disabled = false`).
  - down: `#conn` 빨강 "⚠ 서버 꺼짐"; 음성버튼 비활성(`disabled = true`); voiceOn 이면 `voiceOn=false;
    버튼 active 제거; mic.apply()`(캡처 중단).
- 음성버튼 비활성 스타일: `#voice-toggle:disabled { opacity:.45; cursor:not-allowed; }` (active 펄스 제거).
- (기존 `publisher_disconnected`/`end` 핸들러의 `#conn` 갱신은 `setServerUp(false)` 로 통합.)

---

## F4 — 웹 재배포 일관성 (DO)

- DO 에 `private currentView: string | null = null;` 추가.
- `handlePublisherMessage` 의 `navigate` 케이스: broadcast 전에 `this.currentView = msg.text ?? null;`.
- `attachViewer`(owner) 에서 `lastMicSource` 재전송 다음에:
  `if (this.currentView === "meeting") this.safeSend(ws, this.buildEvent({kind:"navigate", text:"meeting"}));`
  → owner 재접속 시 회의 뷰 복원(navigate 는 여전히 replay 버퍼 미적재, "현재 뷰"만 1회 동기화).
- 회의 종료 시 jarvis 가 navigate("home") 발행 → currentView="home" → 재접속 시 회의 뷰 안 보냄(정상).

**역방향(웹 재배포 중 미팅) 확인**: jarvis 회의 중 → 웹 재배포(reload) → 재접속 → DO 가 자막 replay
+ navigate("meeting")(F4) + (publisher 살아있으면 hello replay→serverUp up, F3). 음성버튼은 voiceOn=false
(새 페이지)라 자동 캡처 안 함 — 사용자가 재탭. 문제 없음.

---

## 데이터 흐름 (jarvis 재시작 시나리오)
```
회의 중 → jarvis 죽음 → publisher 끊김 → DO publisher_disconnected → 웹 빨강+음성off (F3)
jarvis 부팅 → web_pub 연결(hello) + load_mode()=meeting → start_meeting → navigate(meeting)+hello
  → 웹: serverUp up(초록)·회의화면 유지·자막 재개 (F2+F3)
웹 재접속(재배포) → DO replay(자막+hello) + navigate(meeting)(F4) → 회의화면 복원
```

## 테스트
- `tests/test_runtime_state.py`(신규): save/load 라운드트립(tmp path), 허용값 외 무시, 중복 기록 방지.
- `tests/test_conversation.py`(보강): fake `persist_mode` 주입 → `_set_idle`→"idle",
  `start_translate`→"translate" 호출 확인. (start_meeting→"meeting" 은 fake make_meeting 경로로 확인.)
  make_controller 의 deps 에 `persist_mode` 추가(기본 기록 리스트).
- DO/web(F1/F3/F4): `npm run typecheck` + JS 구문검사 + 수동 E2E.

## 검증
- `.venv/bin/python -m pytest -q` 통과 + `import main, runtime_state, conversation`.
- `cd jarvis-web && npm run typecheck` 0, app.html JS 구문 OK.
- 수동(배포/재시작 후):
  - +메뉴 미팅 → 로딩 오버레이 → 회의 진입 시 사라짐(F1).
  - 회의 중 jarvis 재시작 → 웹 빨강+음성off → jarvis 복귀 시 회의 자동 복구·초록(F2/F3).
  - 회의 중 웹 재배포 → 재접속 시 회의 화면 복원(F4).

## 영향 파일
| 파일 | 변경 |
|------|------|
| `runtime_state.py` (신규) | save_mode/load_mode |
| `.gitignore` | `.jarvis_state.json` |
| `conversation.py` | `persist_mode` 주입 + 전이별 호출 |
| `main.py` | persist_mode 배선 + 부팅 복구 분기 |
| `jarvis-web/src/meeting_do.ts` | currentView 추적·재전송, 접속 시 publisher 부재 통지 |
| `jarvis-web/src/static/app.html` | F1 로딩 오버레이 + F3 serverUp/음성비활성 |
| `tests/test_runtime_state.py`(신규), `tests/test_conversation.py` | 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`. origin push 직접.
