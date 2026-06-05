# Deepgram STT 연동 (미팅) 설계

날짜: 2026-06-06

## 목표

설정에서 미팅 STT 가 **Deepgram** 이면 RealtimeSTT 대신 Deepgram 스트리밍을 쓴다. 자막(partial)·
확정(source)→번역 흐름은 기존 그대로 재사용. 설정이 **로컬**이면 기존 RealtimeSTT.

## 확정 결정

- 모델 **Nova-3** + **`language=multi`**(한↔영 코드스위칭, 최신·스트리밍 지원 확인됨).
- Deepgram 연결 실패 시 **RealtimeSTT 로 폴백**(회의가 안 깨지게).
- **jarvis 전용** — 파이썬만 변경(웹/relay/Cloudflare 무변경, 배포 불필요).
- raw `websockets` 사용(이미 의존성). `deepgram-sdk` 미설치 → 추가 안 함.

## 통합 시seam (탐색 확인)

번역 파이프라인이 `_final_q → _consume_finals → _emit("source") + _translate_bg` 로 분리돼 있어,
Deepgram 은 두 콜백만 연결하면 끝:
- interim → `_emit("partial", text)` + `set_status`.
- 발화 종료(final) → `_final_q.put_nowait(text)`.

## 컴포넌트

### config.py

`DEEPSEEK_BASE_URL` 줄(49) 다음에:
```python
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
```
(.env 에 키 이미 추가됨.)

### `deepgram_stt.py` (신규) — `DeepgramSTT`

- 생성자: `DeepgramSTT(api_key, *, language="", on_partial, on_final, on_log=print, connect_timeout=5.0)`.
- URL: `wss://api.deepgram.com/v1/listen?model=nova-3&language={lang}&encoding=linear16&sample_rate=16000&channels=1&interim_results=true&punctuate=true&endpointing=300`
  — `lang` = language 비면 `multi`, 있으면 그 값. 헤더 `Authorization: Token {api_key}`.
- `start()`: `_run`(백오프 재연결 루프) 태스크 기동 + 첫 연결을 `connect_timeout` 내 대기(`_connected`
  Event). 시간 내 미연결이면 `TimeoutError` raise(상위에서 폴백).
- `feed_pcm(pcm16: bytes)`: `_out_q.put_nowait`(가득 차면 드롭).
- `_connect_once`: 연결 → `_connected.set()` → send 루프(`_out_q`→`ws.send(bytes)`)와 recv 루프
  (`async for raw in ws: _handle_dg_message(json.loads(raw))`)를 동시 실행, 하나 끝나면 정리.
- `_handle_dg_message(msg)`(테스트 분리): `type=="Results"` 만. `t = channel.alternatives[0].transcript`.
  - `is_final`: `t` 있으면 `_final_parts.append(t)`. `speech_final` 면 `" ".join(_final_parts)` 를
    `on_final` 로 보내고 비움. 아니면 누적분을 `on_partial`.
  - interim(`is_final` 아님): `(누적분 + " " + t)` 를 `on_partial`.
  - 빈 transcript 는 skip(speech_final 시 누적분 flush 는 처리).
- `close()`: `{"type":"CloseStream"}` 전송 시도 후 stop + 태스크 취소 + ws close.
- 재연결: `_run` 이 끊기면 지수 백오프(RemoteMicReceiver 패턴).

### live_translate.py — MeetingSession

- `__init__`: `self._dg = None`.
- `start()` 재구성:
  - `self._loop`/`self._final_q`/`wb_prompt` 준비(공통).
  - `if settings.get("stt_backend")=="deepgram" and config.DEEPGRAM_API_KEY:` →
    ```python
    from deepgram_stt import DeepgramSTT
    try:
        self._dg = DeepgramSTT(config.DEEPGRAM_API_KEY, language=self.language,
                               on_partial=self._dg_partial, on_final=self._dg_final, on_log=self.log)
        await self._dg.start()
        self.recorder = None
        self.log("🎤 회의 STT: Deepgram (nova-3, multi)")
    except Exception as e:
        self._dg = None
        self.log(f"Deepgram 연결 실패 — 로컬 STT 폴백: {e}")
    ```
  - `self._dg is None` 이면 기존 RealtimeSTT(rec_kwargs + recorder + `_listen_loop` 태스크).
  - `_setup_translator()` + `_consume_finals` 태스크(공통, 양 백엔드).
- `_dg_partial(t)`: dedup(`_partial_last`) 후 `_emit("partial", t)` + `set_status(f"📝 {t[:80]}")`.
  (DeepgramSTT recv 가 메인 루프라 threadsafe 불요 — 직접 호출.)
- `_dg_final(t)`: `self._final_q.put_nowait((t or "").strip())`.
- `feed_block`: `if self._dg is not None: self._dg.feed_pcm(pcm16)` `elif self.recorder: recorder.feed_audio`.
- `stop()`: `if self._dg is not None: await self._dg.close(); self._dg=None`(recorder.shutdown 분기와 병렬).
  나머지(_listen_task 취소·sentinel·consumer·relay) 그대로.

## 엣지 케이스

- **Deepgram 키 없음/연결 실패**: 폴백 → RealtimeSTT(로그). 회의 정상.
- **설정 로컬**: 기존 RealtimeSTT(무변경).
- **무음 구간**: 마이크 tap 이 무음 PCM 도 계속 보내 연결 유지(KeepAlive 불요). 끊기면 _run 재연결.
- **언어**: language 비면 multi(한↔영 코드스위칭).
- **회의 중 설정 변경**: 다음 회의부터(start 시 읽음 — 기존 정책).
- **stop 시 _dg 정리**: CloseStream + ws close + 태스크 취소.

## 테스트 전략

- **단위(jarvis):** `DeepgramSTT._handle_dg_message` — interim→on_partial, is_final 누적,
  speech_final→on_final(누적분), 빈 transcript skip. (가짜 콜백, ws 불요.)
- **수동 E2E:** 설정 STT=Deepgram → jarvis 재시작 → `/meet` → 콘솔 "회의 STT: Deepgram" →
  말하면 partial 자막 + 발화 끝 source + 번역. 키 틀리면 "로컬 STT 폴백" 로그 후 RealtimeSTT 동작.
  설정 STT=로컬 → 기존 RealtimeSTT.

## 비범위

- 배치(비스트리밍) Deepgram. 일반 대화(호출어) STT 의 Deepgram 화(미팅만). 웹/relay 변경.

## 영향 파일

| 파일 | 변경 |
|------|------|
| `config.py` | `DEEPGRAM_API_KEY` |
| `deepgram_stt.py` (신규) | Deepgram 스트리밍 클라이언트(connect/feed/recv/재연결) |
| `live_translate.py` | MeetingSession 이 설정 따라 Deepgram/RealtimeSTT 선택 + feed/stop 라우팅 |
| `tests/test_deepgram_stt.py` (신규) | `_handle_dg_message` 단위 |
