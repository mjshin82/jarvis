# AEC 오디오 백엔드 설계 (macOS VoiceProcessingIO)

날짜: 2026-06-02
상태: 승인됨 (구현 대기)

## 목적

스피커로 나오는 소리(우리 TTS + 음악)가 마이크로 되먹임되어 **호출어 감지와
STT를 저하**시키는 문제를 해결한다. macOS의 VoiceProcessingIO(AEC)를 사용해
**우리 앱이 재생하는 모든 소리를 마이크 입력에서 제거**한다.

### 핵심 제약 (설계의 출발점)

VoiceProcessingIO의 AEC 참조 신호는 **우리 앱이 같은 오디오 엔진으로 렌더한
소리**뿐이다. 다른 앱(Chrome)의 오디오는 참조에 없어 제거되지 않는다.
따라서 음악까지 제거하려면 **음악도 우리 앱에서(엔진을 통해) 재생**해야 한다.

→ 결정: 음악은 **오디오 전용으로 우리 앱에서 재생**한다(유튜브 화면 없음).
사용자가 "소리만 나면 된다"고 확정함.

## 비목표 (YAGNI)

- 유튜브 영상 화면 표시 (오디오 전용으로 충분)
- 스테레오 음악 (모노로 충분)
- Chrome 유지 + 시스템 오디오 캡처 기반 소프트웨어 AEC (더 무겁고 권한 필요 — 제외)
- 비-macOS 지원의 AEC (폴백으로 기존 동작 유지)

## 아키텍처

오디오 입출력을 교체 가능한 **AudioBackend 인터페이스** 뒤로 추상화한다.
AEC용 Swift 데몬 구현을 새로 추가하고, 기존 sounddevice 경로는 폴백으로 남긴다.

```
Python (오케스트레이터: main 상태머신 · audio_io · stt · llm · tts · music)
    │  모두 AudioBackend 인터페이스에만 의존
    ├── AECBackend  ──stdin/stdout(PCM)──▶ audiod (Swift)
    │                                        AVAudioEngine
    │                                          · 입력: VoiceProcessingIO(AEC) → 정제 마이크 PCM 송출
    │                                          · 출력: voice 노드 + music 노드 (= AEC 참조)
    └── SounddeviceBackend (폴백: 기존 sd.InputStream + sd.play, 음악=Chrome)
```

### AudioBackend 인터페이스

```
mic_frames() : async generator → 16kHz mono float32 블록
play_voice(pcm, sr) / flush_voice()      # TTS·효과음
is_speaking() -> bool                      # voice 재생/대기 중 여부 (barge-in·대기 가드용)
play_music(query) -> str / stop_music() -> str
```

기존 VAD·wake·STT·LLM·TTS합성·상태머신 로직은 **변경하지 않는다**.
바뀌는 것은 오디오가 들어오고 나가는 통로뿐이다.

### 컴포넌트

| 파일 | 역할 | 신규/변경 |
|------|------|-----------|
| `audiod.swift` | AVAudioEngine + VoiceProcessingIO. 마이크 정제 PCM 송출 + 재생 수신. swiftc 빌드 | 신규 |
| `audio_backend.py` | AudioBackend 인터페이스 + AECBackend(데몬 클라이언트) + SounddeviceBackend + 선택 로직 | 신규 |
| `audio_io.py` | `sd.InputStream` → `backend.mic_frames()` 소비 (VAD/wake 로직 그대로) | 변경 |
| `player.py` | `sd.play/stop` → `backend.play_voice/flush_voice` | 변경 |
| `music.py` | Chrome 열기 → AEC 백엔드 시 yt-dlp+ffmpeg→엔진 스트리밍, 폴백 시 Chrome | 변경 |
| `scripts/build_audiod.sh` | `swiftc audiod.swift -o audiod -framework AVFoundation` | 신규 |

## 프로토콜 (Python ↔ 데몬)

채널 3개: **stdin**(Python→데몬: 재생 PCM+제어), **stdout**(데몬→Python:
마이크 PCM+이벤트), **stderr**(데몬 로그).

프레이밍(양방향): `[type:1B][length:4B little-endian][payload]`

오디오 포맷 2종 고정:
- 마이크 스트림: **16kHz mono float32**
- 재생 스트림: **48kHz mono float32** (엔진 레이트; TTS 44.1k→48k 리샘플, ffmpeg 48k 출력)

메시지 타입:

| 방향 | type | payload | 용도 |
|------|------|---------|------|
| →데몬 | `PLAY_VOICE` | 48k PCM 청크 | TTS·효과음 (voice 노드) |
| →데몬 | `FLUSH_VOICE` | — | barge-in: voice 즉시 중단+비움 |
| →데몬 | `PLAY_MUSIC` | 48k PCM 청크 | 음악 (music 노드) |
| →데몬 | `STOP_MUSIC` | — | 음악 중단 |
| 데몬→ | `MIC` | 16k PCM 블록 | VAD/wake/STT 입력 |
| 데몬→ | `EVENT` | 작은 JSON | 예: `{"voice":"drained"}` |

- `is_speaking`: voice PCM 송신 시 active=True, `{"voice":"drained"}` 이벤트 수신 시 False.
- 흐름 제어: 데몬은 내부 버퍼가 ~0.5초 미만일 때만 stdin 을 읽음 → 파이프가 차면
  Python write 가 자연스럽게 멈춤(백프레셔). 음악 폭주 방지.

## 음악 재생 (인앱 오디오)

```
play_music(query):
  1. yt-dlp → 상위 1곡 'bestaudio' 스트림 URL + 제목
  2. ffmpeg -i <url> -f f32le -ac 1 -ar 48000 pipe:1   (48k mono f32 PCM 디코드)
  3. 펌프: ffmpeg stdout 청크 → backend.play_music PCM (데몬 백프레셔로 페이싱)
  4. "재생 시작: <제목>" 즉시 반환 (펌프는 백그라운드)
stop_music(): 현재 ffmpeg kill + STOP_MUSIC(데몬 music 노드 비움)
```

- music 모듈이 현재 `(ffmpeg 프로세스, 펌프 태스크)` 보관. 새 재생 시 기존 정리.
- 결과: 음악이 엔진을 거치므로 **AEC가 마이크에서 음악 제거** → 음악 중에도 호출어·STT 깨끗.
- "음악 후 → 호출어 대기" 보수 규칙은 **현행 유지** (AEC 검증 후 완화 검토).
- 폴백(sounddevice): 기존 Chrome 열기/탭 닫기 그대로.

## 폴백 · 설정 · 빌드

백엔드 선택(`config.AEC`, 기본 `auto`):
- `auto`: macOS + swiftc 가용 + 데몬 기동 성공 → AECBackend, 아니면 SounddeviceBackend
- `off`: 무조건 SounddeviceBackend (기존 동작)
- `on`: AECBackend 강제 (불가 시 에러)
- 데몬 기동 실패 시 경고 후 SounddeviceBackend 로 자동 강등 → **절대 깨지지 않음**

빌드:
- AECBackend 시작 시 바이너리 없거나 소스보다 오래되면 **자동 빌드**(swiftc). 없으면 폴백.
- 컴파일된 `audiod` 바이너리는 `.gitignore`, `audiod.swift` 소스만 커밋.

설정 추가(`.env`): `AEC`(auto/on/off), `AUDIOD_PATH`(기본 `./audiod`).

수명: 데몬은 백엔드 init 시 subprocess 기동, 종료 시 main `finally` 정리.
데몬이 죽으면(stdout EOF) 에러 로그 + **1회 자동 재기동** 후 실패 시 폴백.

권한: 마이크 권한 기존 부여됨. VoiceProcessingIO 동일 권한 사용.
의존성 추가: `ffmpeg`(설치됨).

## 테스트 전략

자동(오디오 하드웨어 불필요):
- 프로토콜 프레이밍 인코드/디코드 라운드트립, MIC/EVENT 디먹스
- AECBackend 클라이언트 ↔ **가짜 데몬**(Python): mic_frames 산출, play_voice 프레임,
  flush/is_speaking(drained) 동작
- 백엔드 선택·폴백: AEC=off, 비-macOS 시뮬, 데몬 기동 실패
- 음악 펌프: 가짜 ffmpeg PCM → play_music 청크/stop kill+flush
- 기존 로직 회귀: VAD·wake·상태머신·연속대화·타임아웃을 **FakeBackend** 에 대고 재실행
  (기존 FakeMic/FakePlayer 를 FakeBackend 로 통합)

라이브 스모크(맥에서 마이크·스피커 — 자동화 불가):
- 데몬 기동 + MIC 프레임 송출
- AEC 효과 1: TTS 중 자기 목소리에 안 끌림
- AEC 효과 2: **음악 중 "Hey Jarvis" 호출 + 명령 STT 깨끗** (핵심 목표)
- 전체 흐름: "Hey Jarvis → 음악 틀어줘 → 음악 중 재호출 → 꺼줘"

한계: AEC 실제 제거 성능은 라이브에서만 확인 가능. 자동 테스트는 배선·프로토콜·로직 보장.

## 리스크

- 실시간 IPC 언더런: 음악 PCM 페이싱(백프레셔)으로 완화. ffmpeg 디코드는 실시간보다 빠름.
- 데몬 수명/크래시: stdout EOF 감지 + 1회 재기동 + 폴백.
- VoiceProcessingIO 의 AGC/NS 가 STT/wake 에 미치는 영향: 일반적으로 긍정적(정제)이나 라이브 확인.
- 포맷 변환(48k↔16k↔44.1k) 미스매치: 경계에서만 변환, 포맷 2종으로 고정해 최소화.
