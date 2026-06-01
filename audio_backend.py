"""오디오 입출력 추상화. 구현: SounddeviceBackend(폴백), AECBackend(Swift 데몬)."""
import abc
import asyncio
import platform
import queue
import shutil

import numpy as np

import config


class AudioBackend(abc.ABC):
    @abc.abstractmethod
    async def start(self):
        """백엔드 기동(스트림/데몬/내부 태스크 시작)."""

    @abc.abstractmethod
    async def close(self):
        """정리."""

    @abc.abstractmethod
    async def mic_frames(self):
        """async generator → 16kHz mono float32 블록(np.ndarray)."""

    @abc.abstractmethod
    async def play_voice(self, pcm: np.ndarray, sr: int):
        """TTS/효과음 PCM 재생(순서 보장). 즉시 반환."""

    @abc.abstractmethod
    def flush_voice(self):
        """진행/대기 중 voice 재생 즉시 중단+비움(barge-in)."""

    @abc.abstractmethod
    def is_speaking(self) -> bool:
        """voice 재생 중이거나 대기 중이면 True."""

    @abc.abstractmethod
    async def play_music(self, query: str) -> str:
        """검색어로 음악 재생 시작. 상태 텍스트 반환."""

    @abc.abstractmethod
    async def stop_music(self) -> str:
        """음악 중단. 상태 텍스트 반환."""

    @property
    def supports_inapp_audio(self) -> bool:
        """True 면 음악을 엔진 오디오로 재생(AEC 대상), False 면 외부(Chrome)."""
        return False


class SounddeviceBackend(AudioBackend):
    """기존 동작: sd.InputStream 마이크, sd.play voice(순서 보장), Chrome 음악."""

    def __init__(self):
        self._blocks = queue.Queue()
        self._voice_q: asyncio.Queue = None
        self._playing = False
        self._stream = None
        self._worker = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}")
        self._blocks.put(indata[:, 0].copy())

    async def start(self):
        import sounddevice as sd
        self._voice_q = asyncio.Queue()
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE, channels=config.CHANNELS,
            blocksize=config.BLOCK_SIZE, dtype="float32", callback=self._callback,
        )
        self._stream.start()
        self._worker = asyncio.create_task(self._voice_worker())

    async def close(self):
        if self._worker:
            self._worker.cancel()
        if self._stream:
            self._stream.stop(); self._stream.close()

    async def mic_frames(self):
        loop = asyncio.get_running_loop()
        while True:
            block = await loop.run_in_executor(None, self._blocks.get)
            yield block

    async def _voice_worker(self):
        import sounddevice as sd
        while True:
            pcm, sr = await self._voice_q.get()
            if len(pcm):
                self._playing = True
                try:
                    await asyncio.to_thread(lambda: (sd.play(pcm, sr), sd.wait()))
                finally:
                    self._playing = False

    async def play_voice(self, pcm, sr):
        await self._voice_q.put((pcm, sr))

    def flush_voice(self):
        import sounddevice as sd
        while not self._voice_q.empty():
            try:
                self._voice_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        sd.stop()

    def is_speaking(self):
        return self._playing or (self._voice_q is not None and not self._voice_q.empty())

    async def play_music(self, query):
        return await _chrome_play_music(query)

    async def stop_music(self):
        return await _chrome_stop_music()


async def _chrome_play_music(query: str) -> str:
    from music import chrome_play
    return await chrome_play(query)


async def _chrome_stop_music() -> str:
    from music import chrome_stop
    return await chrome_stop()


class AECBackend(AudioBackend):
    """Swift 데몬 클라이언트 — 실제 구현은 이후 태스크. 지금은 생성만 가능."""
    async def start(self): raise NotImplementedError
    async def close(self): ...
    async def mic_frames(self):
        if False:
            yield
    async def play_voice(self, pcm, sr): raise NotImplementedError
    def flush_voice(self): ...
    def is_speaking(self): return False
    async def play_music(self, query): raise NotImplementedError
    async def stop_music(self): raise NotImplementedError


def _aec_available() -> bool:
    """macOS + swiftc(빌드용) 또는 xcrun 존재."""
    return bool(shutil.which("swiftc")) or shutil.which("xcrun") is not None


def make_backend() -> AudioBackend:
    mode = config.AEC
    if mode == "off":
        return SounddeviceBackend()
    is_mac = platform.system() == "Darwin"
    if mode == "on":
        if not is_mac:
            raise RuntimeError("AEC=on 이지만 macOS 가 아닙니다.")
        return AECBackend()
    # auto
    if is_mac and _aec_available():
        return AECBackend()
    return SounddeviceBackend()
