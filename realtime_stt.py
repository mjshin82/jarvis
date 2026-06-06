# realtime_stt.py
"""RealtimeSTT recorder 공용 래퍼.

streaming_stt(일반 대화)와 live_translate(회의 RT 폴백)가 공유:
recorder 생성·partial dedup/threadsafe 마샬링·listen 루프·feed·shutdown,
그리고 (옵션) 공급 정체 시 무음 주입 플러시.
"""
import asyncio
import time

import numpy as np

_FLUSH_SILENCE_S = 1.0   # 강제 final 유도용 무음 길이(초)


def to_pcm16(block) -> bytes:
    """float32[-1,1] → int16 LE PCM bytes."""
    return (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class RealtimeSTTAdapter:
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="", initial_prompt=None, on_log=print,
                 recorder_factory=None, clock=time.monotonic,
                 silence_flush=False, flush_after=1.2):
        self.on_partial = on_partial
        self.on_final = on_final
        self.model = model
        self.realtime_model = realtime_model
        self.language = language
        self.initial_prompt = initial_prompt
        self.log = on_log
        self._recorder_factory = recorder_factory
        self._clock = clock
        self._silence_flush = silence_flush
        self._flush_after = flush_after
        self.recorder = None
        self._loop = None
        self._listen_task = None
        self._flush_task = None
        self._partial_last = ""
        self._last_feed_ts = 0.0

    # --- feed ---
    def feed_pcm16(self, pcm16: bytes) -> None:
        if self.recorder is None:
            return
        self.recorder.feed_audio(pcm16, 16000)
        self._last_feed_ts = self._clock()

    def feed_block(self, block) -> None:
        self.feed_pcm16(to_pcm16(block))

    # --- partial (레코더 스레드에서 호출 → 메인 루프로 마샬링) ---
    def _on_partial(self, text):
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_partial, text)
        else:
            self.on_partial(text)

    # --- final (메인 루프에서) ---
    def _dispatch_final(self, text):
        self._partial_last = ""
        if text:
            self.on_final(text)

    # --- 무음 플러시 ---
    def _maybe_flush(self, now) -> bool:
        if self.recorder is None or not self._partial_last:
            return False
        if now - self._last_feed_ts < self._flush_after:
            return False
        silence = np.zeros(int(16000 * _FLUSH_SILENCE_S), dtype=np.int16).tobytes()
        try:
            self.recorder.feed_audio(silence, 16000)
        except Exception:
            return False
        self._last_feed_ts = now
        return True

    async def _flush_loop(self):
        try:
            while True:
                await asyncio.sleep(0.2)
                self._maybe_flush(self._clock())
        except asyncio.CancelledError:
            return

    # --- 레코더 생성 ---
    def _make_recorder(self):
        if self._recorder_factory is not None:
            return self._recorder_factory(self._on_partial)
        from RealtimeSTT import AudioToTextRecorder
        kwargs = dict(
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
        if self.initial_prompt:
            kwargs["initial_prompt"] = self.initial_prompt
            kwargs["initial_prompt_realtime"] = self.initial_prompt
        return AudioToTextRecorder(**kwargs)

    async def _listen_loop(self):
        try:
            while True:
                def _final_cb(t):
                    try:
                        self._loop.call_soon_threadsafe(self._dispatch_final, (t or "").strip())
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

    # --- 라이프사이클 ---
    async def start(self):
        self._loop = asyncio.get_running_loop()
        self.recorder = self._make_recorder()
        self._last_feed_ts = self._clock()
        self._listen_task = asyncio.create_task(self._listen_loop())
        if self._silence_flush:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def close(self):
        for task in (self._flush_task, self._listen_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._flush_task = None
        self._listen_task = None
        if self.recorder is not None:
            try:
                self.recorder.shutdown()
            except Exception:
                pass
            self.recorder = None
