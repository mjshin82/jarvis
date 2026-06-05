# Gladia STT 연동 (Deepgram 대체) 설계

날짜: 2026-06-06

## 목표

미팅 STT 백엔드를 Deepgram → **Gladia**(solaria-1, 한↔영 code-switching)로 교체. Deepgram 은 완전 제거.
자막(partial)·확정(final)→번역 흐름은 기존 그대로.

## 확정 (라이브 검증됨)

- Gladia 2단계: `POST /v2/live?region=us-west`(X-Gladia-Key, config) → `{url}` → `websockets.connect(url)`.
  init 201 + ws 연결 + 메시지 수신 확인.
- 모델 **solaria-1**, languages **["ko","en"]** + **code_switching:true** (.env 조절).
- Deepgram 전부 제거. 연결 실패 시 RealtimeSTT 폴백.
- 범위: jarvis(재시작) + 웹 app.html 설정 라디오(배포).

## 컴포넌트

### 제거 (Deepgram)
- `deepgram_stt.py`, `tests/test_deepgram_stt.py` 삭제.
- config: `DEEPGRAM_API_KEY`/`MEET_DEEPGRAM_MODEL`/`MEET_DEEPGRAM_LANGUAGE` 제거.
- `requirements.txt`: `deepgram-sdk` 제거(`requests` 추가/확인).
- `.env`: DEEPGRAM 제거, GLADIA_API_KEY 추가(완료).

### config.py
```python
GLADIA_API_KEY = os.getenv("GLADIA_API_KEY", "")
MEET_GLADIA_MODEL = os.getenv("MEET_GLADIA_MODEL", "solaria-1")
MEET_GLADIA_LANGUAGES = os.getenv("MEET_GLADIA_LANGUAGES", "ko,en")
```

### `gladia_stt.py` (신규) — `GladiaSTT`
- `__init__(api_key, *, model="solaria-1", languages=("ko","en"), on_partial, on_final, on_log=print, connect_timeout=5.0)`.
- `_init_session()`(동기, 스레드): `requests.post(f"{BASE}/v2/live?region=us-west", headers={"X-Gladia-Key":key}, json=_config(), timeout=5)`. !ok 면 raise.
- `_config()`: `{encoding:"wav/pcm", bit_depth:16, sample_rate:16000, channels:1, model, language_config:{languages:list, code_switching:True}, messages_config:{receive_partial_transcripts:True, receive_final_transcripts:True}}`.
- `start()`: `_run` 태스크 + `_connected` 대기(타임아웃 raise).
- `_connect_once()`: `resp = await asyncio.to_thread(self._init_session)` → `async with websockets.connect(resp["url"]) as ws:` → `_connected.set` → send/recv 동시.
- `feed_pcm(bytes)`: 큐 적재.
- `_send_loop`: 큐 → `ws.send(bytes)`.
- `_recv_loop`: `async for raw in ws: _handle_gladia_message(json.loads(raw))`.
- `_handle_gladia_message(msg)`(테스트 분리): `type=="transcript"` → `data.utterance.text` → `is_final` 이면 `on_final`, 아니면 `on_partial`. (Gladia utterance.text 는 발화 전체 — 누적 불필요.)
- `close()`: `{"type":"stop_recording"}` 전송 시도 후 stop + 태스크 취소.
- 재연결: `_run` 백오프(끊기면 재-init+재연결).

### settings.py
- `stt_backend` 기본 **"gladia"**, 허용 `{"gladia","local"}`. (기존 setting.yaml "deepgram" → 유효목록 제외 → load 시 기본 gladia.)

### live_translate.py — MeetingSession
- `_dg` → 백엔드 무관 `_stt`(+ `_dg_partial`→`_stt_partial`, `_dg_final`→`_stt_final`).
- start(): `settings.get("stt_backend")=="gladia" and config.GLADIA_API_KEY` 면
  `GladiaSTT(config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL,
  languages=[s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()],
  on_partial=self._stt_partial, on_final=self._stt_final, on_log=self.log)` → start → recorder=None →
  로그 "🎤 회의 STT: Gladia (...)". 실패 시 `_stt=None` → RealtimeSTT 폴백.
- feed_block/stop: `_stt` 라우팅.

### web app.html
- 설정 모달 STT 라디오: `value="deepgram"` "Deepgram" → **`value="gladia"` "Gladia"**.
- `curSettings()` 의 stt_backend 기본 `"deepgram"` → `"gladia"`.

## 데이터 흐름
```
feed_block → GladiaSTT.feed_pcm → ws → transcript(partial)→_emit("partial") /
  transcript(is_final)→_final_q→_consume_finals→source+번역(기존)
```

## 엣지
- Gladia init/연결 실패 → RealtimeSTT 폴백(로그).
- 설정 로컬 → RealtimeSTT.
- 무음에도 마이크 tap 이 PCM 계속 전송 → 세션 유지.
- 기존 setting.yaml "deepgram" → 자동 gladia.
- 회의 중 설정 변경 → 다음 회의부터.

## 테스트
- 단위: `GladiaSTT._handle_gladia_message` — transcript+is_final→on_final, partial→on_partial, 비transcript/빈텍스트 무시. settings(gladia 기본/허용).
- 수동 E2E: 설정 STT=Gladia → 재시작 → /meet → "회의 STT: Gladia" → 한↔영 자막+번역.

## 비범위
- Gladia 번역/NER/sentiment 기능. 일반 대화 STT.

## 영향 파일
| 파일 | 변경 |
|------|------|
| `gladia_stt.py` (신규) | Gladia 스트리밍 클라이언트 |
| `deepgram_stt.py`, `tests/test_deepgram_stt.py` | 삭제 |
| `config.py` | DEEPGRAM_* 제거, GLADIA_* 추가 |
| `requirements.txt` | deepgram-sdk 제거, requests |
| `settings.py` | stt_backend gladia/local, 기본 gladia |
| `live_translate.py` | GladiaSTT 사용, `_stt` 일반화 |
| `jarvis-web/src/static/app.html` | STT 라디오 Gladia |
| `tests/test_gladia_stt.py` (신규), `tests/test_settings.py` | 갱신 |
