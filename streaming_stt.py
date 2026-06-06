# streaming_stt.py
"""일반 대화용 스트리밍 STT — RealtimeSTT 래퍼(번역 없음).

live_translate.MeetingSession 의 인식부를 본뜸. mic.router tap 으로 블록을 연속 피드받아
partial(조합중)·final 콜백을 낸다. 회의 통합은 비범위(중복 최소, 안전 우선).
테스트 용이성을 위해 recorder_factory 주입 허용.
"""
import asyncio
import time

import numpy as np

# 공급이 멈췄을 때 강제 final 을 끌어내려 주입할 무음 길이(초).
# post_speech_silence_duration(0.7s) 보다 충분히 길게.
_FLUSH_SILENCE_S = 1.0


class StreamingRecognizer:
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="ko", on_log=print, recorder_factory=None,
                 clock=time.monotonic, flush_after=1.2):
        self.on_partial = on_partial
        self.on_final = on_final
        self.model = model
        self.realtime_model = realtime_model
        self.language = language
        self.log = on_log
        self._recorder_factory = recorder_factory
        self._clock = clock
        self._flush_after = flush_after   # 마지막 공급 후 이 시간 지나면 무음 주입
        self.recorder = None
        self._loop = None
        self._final_q = None
        self._listen_task = None
        self._consumer_task = None
        self._flush_task = None
        self._partial_last = ""
        self._last_feed_ts = 0.0

    def feed_block(self, block) -> None:
        """mic.router tap 이 매 블록 호출 — float32[-1,1] 16kHz → int16 PCM bytes 주입.
        (float32 를 그대로 feed_audio 에 주면 내부 astype(int16) 로 0 이 됨)"""
        if self.recorder is None:
            return
        pcm16 = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        self.recorder.feed_audio(pcm16, 16000)
        self._last_feed_ts = self._clock()

    def _maybe_flush(self, now) -> bool:
        """발화 partial 이 떠 있는데 공급이 끊겼으면 무음을 주입해 end-of-speech 를
        강제로 감지시킨다(→ final). 모바일 마이크가 발화 직후 끊겨도 멈추지 않게.
        주입했으면 True. 정상(연속 공급)일 땐 gap 미달로 항상 False."""
        if self.recorder is None:
            return False
        if not self._partial_last:        # 확정 대기 중인 발화 없음
            return False
        if now - self._last_feed_ts < self._flush_after:
            return False
        n = int(16000 * _FLUSH_SILENCE_S)
        silence = np.zeros(n, dtype=np.int16).tobytes()
        try:
            self.recorder.feed_audio(silence, 16000)
        except Exception:
            return False
        self._last_feed_ts = now          # 다음 gap 까지 재주입 방지
        return True

    async def _flush_loop(self):
        try:
            while True:
                await asyncio.sleep(0.2)
                self._maybe_flush(self._clock())
        except asyncio.CancelledError:
            return

    def _on_partial(self, text):
        """RealtimeSTT 스레드에서 호출 — dedup 후 메인 루프로 안전 위탁."""
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_partial, text)
        else:
            self.on_partial(text)

    def _make_recorder(self):
        if self._recorder_factory is not None:
            return self._recorder_factory(self._on_partial)
        from RealtimeSTT import AudioToTextRecorder
        return AudioToTextRecorder(
            model=self.model,
            realtime_model_type=self.realtime_model,
            enable_realtime_transcription=True,
            on_realtime_transcription_update=self._on_partial,
            language=self.language,
            spinner=False,
            post_speech_silence_duration=0.7,
            silero_sensitivity=0.4,
            webrtc_sensitivity=3,
            device="cpu",
            compute_type="int8",
            level=30,
            use_microphone=False,
        )

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._final_q = asyncio.Queue()
        self.recorder = self._make_recorder()
        self._last_feed_ts = self._clock()
        self._consumer_task = asyncio.create_task(self._consume_finals())
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def _consume_finals(self):
        while True:
            text = await self._final_q.get()
            if text is None:
                return
            self._partial_last = ""
            if text:
                res = self.on_final(text)
                if asyncio.iscoroutine(res):
                    await res

    async def _listen_loop(self):
        try:
            while True:
                def _final_cb(t):
                    try:
                        self._loop.call_soon_threadsafe(self._final_q.put_nowait, (t or "").strip())
                    except Exception:
                        pass
                await asyncio.to_thread(self.recorder.text, _final_cb)
        except asyncio.CancelledError:
            return
        except Exception as ex:
            try:
                self.log(f"[stt] listen loop error: {ex}")
            except Exception:
                pass

    async def close(self):
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.recorder is not None:
            try:
                self.recorder.shutdown()
            except Exception:
                pass
            self.recorder = None
        if self._final_q is not None:
            await self._final_q.put(None)
        if self._consumer_task and not self._consumer_task.done():
            try:
                await asyncio.wait_for(self._consumer_task, timeout=2.0)
            except Exception:
                self._consumer_task.cancel()
        self._consumer_task = None
