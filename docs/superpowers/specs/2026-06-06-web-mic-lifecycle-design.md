# 웹 마이크 수명주기 캡슐화 (mic 객체 + 파생 상태) 설계

날짜: 2026-06-06

## 목표

jarvis-web(`app.html`)의 마이크 캡처 수명주기를 단일 `mic` 객체로 캡슐화하고,
"캡처해야 하는가"를 **하나의 파생 상태**로 단일화하여 desync(상태 불일치)와
stale-소켓 교차종료를 구조적으로 제거한다. (이전 아키텍처 리뷰의 D+E 항목.)

성격: **리팩터 + 불변식 강제**. 외부 UX 는 보존하되, 캡처 시작/중단을 단일 진입점으로
모아 다음 위험을 닫는다 — micStart 가 기존 소켓을 안 닫던 누수, onclose/loseMic 의
모듈전역 `micWS` 교차종료, `micOn`/`voiceOn` 불일치, navigate(회의↔홈) 시 mic 미재조정,
`micSource` 의 이중 소스(클릭+서버).

## 배경 (현재의 부채)

`app.html` 의 mic 로직(`micStart`/`micStop`/`loseMic` + voice/meeting 토글 + handle 의
navigate/mic_source/kicked)은 모듈전역 `micWS, micNode, micStream, micOn, voiceOn,
micSource, wakeLock` 를 흩어서 변이한다.
- `micStart` 가 기존 `micWS` 를 닫지 않고 새로 생성 → 소켓 누수/중복.
- `onclose`→`loseMic`→`micStop` 이 모듈전역 `micWS`(현재 값)를 닫음 → stale 소켓이
  산 소켓을 닫는 교차종료.
- `micOn` 과 `voiceOn` 이 어긋날 수 있음.
- `navigate` 시 mic 상태 재조정 없음.
- `micSource` 가 클릭(낙관)과 서버 이벤트 양쪽에서 쓰여 단일 진실원 아님.

## 비범위 (YAGNI)

- 서버(`meeting_do.ts`) 변경 없음 — DO 는 이미 jarvis 소스전환을 `mic_source` 로
  브로드캐스트한다(재조정 입력원으로 그대로 사용).
- 자동 JS 단위테스트 하니스 도입 안 함 — 저장소에 JS 테스트 인프라가 없고, mic 표면이
  작으며, 오디오/iOS autoplay 같은 실위험은 실기기 검증이 본질. 검증은 **수동 E2E**.
- AudioContext/재생/keep-alive 로직 변경 없음(공유만).
- 구독(`/subscribe`) 채널의 `kicked` 처리 변경 없음(별개).

## 아키텍처

### 의도(intent) 변수 — `app.html` 모듈 스코프
캡처를 "해야 하는가"를 결정하는 사용자/뷰 의도:
- `view` — 현재 뷰(`home`/`meeting`), `document.body.dataset.view` 로 조회.
- `voiceOn` — 홈 음성 핸즈프리 의도(기존).
- `micSource` — 회의 입력 의도 `"system"|"remote"`(기존, 서버 계약명).

### 파생 규칙 (단일 진실)
```js
shouldCapture = (view==="home"   && voiceOn) ||
                (view==="meeting" && micSource==="remote")
```

### `mic` 객체 — 캡처 수명주기만 소유 (`createMic(deps)` 클로저 팩토리)
- 내부 상태(비노출): `ws, node, stream, wakeLock, gen(정수), capturing(bool)`.
- 주입 deps:
  - `shouldCapture()` → bool (위 파생 규칙)
  - `onLost(reason)` — 예기치 않은 캡처 끊김. reason ∈ `"auth"|"kicked"|"closed"|"mic-permission"`
  - `getPw()`, `host`, `name` — 소켓 URL/인증
  - `ensureAudio()`, `getAudioCtx()` — 공유 AudioContext
  - `floatToInt16(f32)`, `downsample(buf, srcRate)` — 기존 PCM 변환 유틸
- 공개 API (이것만):
  - `mic.apply()` — **유일한 진입점**, 멱등. `shouldCapture()` 와 `capturing` 비교해 시작/중단.
  - `mic.isCapturing()` → bool
- `micOn` 변수 제거 → "마이크 켜짐"은 `capturing` 단일화.
- mic 객체가 자체 `visibilitychange` 리스너로 wakeLock 재획득 책임.

### 제어 명령은 클릭 핸들러 잔류
`listen_start/stop`, `mic_phone/system` 전송은 mic 객체 밖(핸들러)에 둔다. mic 객체는
오디오 캡처(WS+노드)만.

## generation 가드 + apply (핵심)

```js
async function _start() {
  const myGen = ++gen;          // 새 세대 — 이전 콜백 전부 무효화
  capturing = true;
  _teardownSocket();            // close-before-open
  ws = new WebSocket(`${proto}//${host}/mic/${enc(name)}?token=${enc(pw)}`);
  ws.binaryType = "arraybuffer";
  let opened = false;
  ws.onopen    = () => { if (myGen!==gen) return; opened=true; ws.send(JSON.stringify({kind:"mic_start"})); };
  ws.onmessage = (e) => { if (myGen!==gen) return; try { if (JSON.parse(e.data).kind==="kicked") onLost("kicked"); } catch {} };
  ws.onclose   = (e) => { if (myGen!==gen) return;            // stale close 무시(교차종료 차단)
                          if (e.code===1008 || (!opened && e.code===1006)) onLost("auth");
                          else onLost("closed"); };
  try { stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}}); }
  catch (err) { if (myGen===gen) onLost("mic-permission"); return; }
  if (myGen!==gen) { stream.getTracks().forEach(t=>t.stop()); stream=null; return; }  // await 중 세대 바뀜
  ensureAudio();
  const ctx = getAudioCtx();
  const srcNode = ctx.createMediaStreamSource(stream);
  node = ctx.createScriptProcessor(4096, 1, 1);
  srcNode.connect(node); node.connect(ctx.destination);
  await _requestWakeLock(myGen);
  node.onaudioprocess = (ev) => {
    if (myGen!==gen) return;
    if (!ws || ws.readyState!==1) return;
    ws.send(floatToInt16(downsample(ev.inputBuffer.getChannelData(0), ctx.sampleRate)).buffer);
  };
}

function _stop() {
  gen++;                        // 진행 중 모든 콜백(자기 onclose 포함) 무효화
  capturing = false;
  _teardownAudio();             // node.disconnect/onaudioprocess=null/null, stream tracks stop
  _teardownSocket(true);        // open 이면 mic_stop 전송 후 ws.close(), ws=null
  _releaseWakeLock();
  const ctx = getAudioCtx();
  if (ctx && ctx.state==="suspended") ctx.resume();   // iOS 재생 유지
}

function apply() {
  const want = shouldCapture();
  if (want && !capturing) _start();
  else if (!want && capturing) _stop();
}
```

**desync 차단 메커니즘:**
- 교차종료 차단: `_stop()` 이 `gen++` 하므로 그 close 의 onclose 는 `myGen!==gen` 로 무시
  → 의도적 중단이 `onLost` 오발화 안 함. 과거 소켓의 늦은 close 가 현재 소켓 무영향.
- stale 오디오 프레임 차단: 이전 세대 `onaudioprocess` 즉시 bail.
- getUserMedia 경쟁: await 사이 세대 바뀌면 stream 폐기.

### `onLost(reason)` 앱 처리
```js
function onMicLost(reason) {
  if (curView()==="home") { voiceOn = false; updateVoicePill(); }
  else { micSource = "system"; updateMeetingToggle(); }   // 의도 off
  mic.apply();                                            // 재조정(중단 확정)
  if (reason==="auth") { localStorage.removeItem(ADMIN_KEY); showLogin(); }
  else if (reason==="mic-permission") alert("마이크 권한 실패 — 권한을 허용해주세요.");
  // kicked/closed → 조용히 의도만 off(원본 동일, 재시도 안 함)
}
```

## 통합 (의도 갱신 → apply)

- **홈 음성 토글**: `voiceOn=!voiceOn; updateVoicePill(); sendControl(listen_start/stop); mic.apply();`
- **회의 소스 토글(낙관)**: `micSource = flip; updateMeetingToggle(); sendControl(mic_phone/system); mic.apply();`
- **navigate(handle case)**: `showView(ev.text); mic.apply();` — 뷰 전환 시 캡처 재조정.
- **서버 mic_source(handle case, 서버 권위)**: `micSource = ev.source||"system"; updateMeetingToggle(); mic.apply();`

제거: `micOn` 변수, `micStart`/`micStop`/`loseMic`. 대체: `mic.apply()` / `onMicLost`.
`micOn` 참조처(visibilitychange wakeLock 등)는 mic 객체 내부로 흡수.

불변식: 캡처는 오직 `apply()` 로만 시작/중단. 어떤 핸들러도 소켓을 직접 만지지 않는다.

## 데이터 흐름
```
사용자/서버 동작 → 의도 변수(view/voiceOn/micSource) 갱신 → mic.apply()
  apply: shouldCapture() vs capturing → _start()/_stop() (gen 가드)
  예기치 않은 끊김 → onLost(reason) → 의도 off + apply + (auth/permission 알림)
서버 mic_source 이벤트 → micSource 권위 갱신 → apply (낙관적 클릭과 수렴)
```

## 엣지케이스
- 빠른 더블탭: apply 멱등 + gen 가드 → 유령 소켓 없음.
- 서버 kick: onLost("kicked") → 의도 off.
- 네트워크 끊김(open 후 1006): onLost("closed") → 의도 off, 재시도 안 함.
- 인증(1008/open 전 1006): onLost("auth") → 로그아웃+로그인.
- 마이크 권한 거부: onLost("mic-permission") → 알림+의도 off.
- 회의 종료(navigate home) 중 폰 캡처: navigate→apply→ 홈&voiceOn=false → 캡처 중단(본체로 안 샘).
- 홈↔회의 전환: voiceOn 보존, 복귀 시 apply 로 재개(의도 일관 — 의도된 동작).
- iOS 화면잠금→해제: mic 내부 visibilitychange 로 wakeLock 재획득.
- 페이지 새로고침: voiceOn=false 로 시작, 캡처 미재개(서버 타임아웃) — 원본 동일.

## 의도된 동작 변경 (불변식 강제로 바뀌는 부분)
- navigate 가 캡처를 재조정한다(이전엔 안 함) → 회의 종료 후 폰 캡처가 자동 중단.
- 캡처 시작/중단이 단일 `apply()` 경로 → micOn/voiceOn 불일치 불가.
- 의도적 중단이 onLost 를 발화하지 않음(gen 가드).

## 테스트 / 검증
JS 단위테스트 없음(인프라 미보유). **수동 E2E 체크리스트(폰/iOS 실기기, 배포 후):**
1. 홈 음성 ON→말→응답→OFF — 캡처·버튼 일치
2. 빠른 더블탭 ON/OFF/ON — 최종 상태 정확, 끊김 없음
3. 회의 입장→폰 토글(입력됨)→시스템 토글(중단)
4. 회의 중 폰 입력→회의 종료→홈에서 본체로 안 새고 캡처 중단
5. 두 기기 mic 경합(kick) — 뺏긴 쪽 off + 알림
6. 기내모드 토글 — 인지 + 의도 off, 무한재시도 X
7. 인증 만료 → 로그인 / 마이크 권한 거부 → 알림
8. iOS 화면잠금→해제 — wakeLock 재획득, 캡처 유지

기존 파이썬 테스트(95개)는 영향 없음(웹 전용 변경). `cd jarvis-web && npm run typecheck` 로
TS 영향 없음 확인(app.html 은 문자열 번들이라 타입영향 없음 — 빌드 깨짐만 확인).

## 영향 파일
| 파일 | 변경 |
|------|------|
| `jarvis-web/src/static/app.html` | mic 객체(createMic) 도입, 의도+apply 패턴, micOn/micStart/micStop/loseMic 제거 |

배포: `cd jarvis-web && npx wrangler deploy` (사용자 확인 후). origin push 는 사용자가 직접.
