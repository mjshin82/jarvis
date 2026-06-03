"""회의 모드 — RealtimeSTT 로 실시간 자막을 받고, 발화 단위로 한국어 번역.

평상시 STT(faster-whisper, sd 기반)와 별개. /meet 진입 시에만 활성화하고
종료 시 깔끔히 shutdown. 메모리·CPU 부담이 큰 라이브러리라 회의 동안만 띄운다.

흐름:
  시작 → RealtimeSTT 시작 → 부분 결과는 [자막] 한 줄로 흘러가게 표시
                          → 발화 끝나면 확정 텍스트 + 한국어 번역 출력
  종료 → RealtimeSTT shutdown
"""
import asyncio
import sys

import pyaudio


def _pick_physical_mic() -> int | None:
    """BlackHole/Teams 등 가상장치 회피 후 첫 물리 마이크 인덱스."""
    p = pyaudio.PyAudio()
    skip = ("blackhole", "loopback", "aggregate", "teams", "soundflower")
    chosen = None
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] <= 0:
                continue
            if any(s in info["name"].lower() for s in skip):
                continue
            chosen = i
            break
    finally:
        p.terminate()
    return chosen


class MeetingSession:
    """RealtimeSTT 한 인스턴스를 들고 다닌다. 메인 이벤트 루프에서 콜백을
    안전하게 다루기 위해 asyncio.Queue 로 final 텍스트를 넘긴다."""

    def __init__(self, *, log, set_status, translate_async, model: str = "small",
                 realtime_model: str = "tiny", language: str = ""):
        self.log = log
        self.set_status = set_status
        self.translate_async = translate_async   # async (text) -> ko
        self.model = model
        self.realtime_model = realtime_model
        self.language = language
        self.recorder = None
        self._loop = None
        self._final_q: asyncio.Queue[str | None] | None = None
        self._consumer_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        self._partial_last = ""

    async def start(self) -> None:
        from RealtimeSTT import AudioToTextRecorder   # 회의 모드 진입할 때만 import

        self._loop = asyncio.get_running_loop()
        self._final_q = asyncio.Queue()
        mic_idx = _pick_physical_mic()

        self.recorder = AudioToTextRecorder(
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
            input_device_index=mic_idx,
            level=30,   # WARNING 만
        )

        # 콜백→큐 브리지: 메인 루프에서 안전하게 처리
        self._consumer_task = asyncio.create_task(self._consume_finals())
        # 발화 단위 listen 루프: recorder.text() 블로킹 → 스레드로
        self._listen_task = asyncio.create_task(self._listen_loop())
        self.log("🎤 회의 모드 시작. 끝내려면 /stop.")

    async def stop(self) -> None:
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
        self.log("🎤 회의 모드 종료.")

    # --- 내부 ---

    def _on_partial(self, text: str):
        """RealtimeSTT 스레드에서 호출. 같은 줄에 덮어쓰기는 prompt_toolkit 과
        잘 안 맞으므로 의미 단위로만 출력(긴 변화가 있을 때)."""
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        # 너무 자주 찍히지 않게: 텍스트가 충분히 더 길어졌을 때만
        # (큐 영역에 표시하는 게 깔끔하지만 일단은 status 영역에 흘림)
        try:
            # 메인 루프에 set_status 위탁
            self._loop.call_soon_threadsafe(self.set_status, f"📝 {text[:80]}")
        except Exception:
            pass

    async def _listen_loop(self):
        """recorder.text(cb) 는 블로킹 → asyncio.to_thread 로 감싼다.
        한 번 호출에 한 발화. 끝나면 텍스트가 콜백으로 들어옴."""
        try:
            while True:
                # 콜백 안에서 큐에 넣음 — 스레드 안전
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
                self.log(f"[meet] listen loop error: {ex}")
            except Exception:
                pass

    async def _consume_finals(self):
        """확정 발화 처리 — 출력 + 번역. 메인 이벤트 루프에서 안전하게."""
        while True:
            item = await self._final_q.get()
            if item is None:
                return
            text = item.strip()
            if not text:
                continue
            # status 비우고 한 줄로 확정 표시
            try:
                self.set_status(None)
            except Exception:
                pass
            self._partial_last = ""
            self.log(f"🧑 {text}")
            # 번역은 백그라운드 — 다음 발화 처리에 영향 X
            asyncio.create_task(self._translate_bg(text))

    async def _translate_bg(self, text: str):
        try:
            ko = await self.translate_async(text)
        except Exception as ex:
            self.log(f"[meet] translate error: {ex}")
            return
        if ko:
            self.log(f"🌐 {ko}")
