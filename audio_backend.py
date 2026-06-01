"""오디오 입출력 추상화. 구현: SounddeviceBackend(폴백), AECBackend(Swift 데몬)."""
import abc
import asyncio
import platform
import queue
import shutil

import numpy as np

import audio_proto as proto
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
    """Swift 데몬 클라이언트 — audio_proto 프레이밍으로 stdin/stdout 통신."""

    def __init__(self, cmd=None):
        self._cmd = cmd
        self._proc = None
        self._reader_task = None
        self._mic_q: asyncio.Queue = None
        self._dec = proto.FrameDecoder()
        self._voice_active = 0
        self._music = None

    @property
    def supports_inapp_audio(self):
        return True

    def _resolve_cmd(self):
        if self._cmd:
            return self._cmd
        import os
        import subprocess
        bin_path = config.AUDIOD_PATH
        need_build = (not os.path.exists(bin_path) or
                      os.path.getmtime(config.AUDIOD_SRC) > os.path.getmtime(bin_path))
        if need_build:
            subprocess.run(["swiftc", config.AUDIOD_SRC, "-o", bin_path,
                            "-framework", "AVFoundation"], check=True)
        return [bin_path]

    async def start(self):
        self._mic_q = asyncio.Queue()
        cmd = self._resolve_cmd()
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        while True:
            chunk = await self._proc.stdout.read(4096)
            if not chunk:
                break
            self._dec.feed(chunk)
            for mtype, payload in self._dec:
                if mtype == proto.MIC:
                    await self._mic_q.put(proto.pcm_to_array(payload).copy())
                elif mtype == proto.EVENT:
                    ev = proto.decode_event(payload)
                    if ev.get("voice") == "drained":
                        self._voice_active = 0

    async def close(self):
        if self._music:
            await self.stop_music()
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    async def mic_frames(self):
        while True:
            yield await self._mic_q.get()

    async def _send(self, data: bytes):
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def play_voice(self, pcm, sr):
        pcm48 = _resample_mono(pcm, sr, 48000)
        self._voice_active += 1
        await self._send(proto.encode_pcm(proto.PLAY_VOICE, pcm48))

    def flush_voice(self):
        self._voice_active = 0
        self._proc.stdin.write(proto.encode(proto.FLUSH_VOICE))

    def is_speaking(self):
        return self._voice_active > 0

    async def play_music(self, query):
        from music import resolve_track, start_ffmpeg_pump
        await self.stop_music()
        track = await resolve_track(query)
        if not track:
            return f"'{query}' 에 맞는 음악을 찾지 못했습니다."
        vid, title = track
        self._music = await start_ffmpeg_pump(vid, self._send_music)
        return f"재생 시작: {title}"

    async def _send_music(self, pcm48):
        await self._send(proto.encode_pcm(proto.PLAY_MUSIC, pcm48))

    async def stop_music(self):
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(proto.encode(proto.STOP_MUSIC))
        if self._music:
            from music import stop_ffmpeg_pump
            await stop_ffmpeg_pump(self._music)
            self._music = None
            return "음악을 껐습니다."
        return "재생 중인 음악이 없습니다."


def _resample_mono(pcm, sr, target):
    pcm = np.ascontiguousarray(pcm, dtype=np.float32).reshape(-1)
    if sr == target:
        return pcm
    import librosa
    return np.ascontiguousarray(librosa.resample(pcm, orig_sr=sr, target_sr=target),
                                dtype=np.float32)


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
