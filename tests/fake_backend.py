import numpy as np
from audio_backend import AudioBackend


class FakeBackend(AudioBackend):
    """테스트용: 스크립트된 마이크 프레임을 내보내고 호출을 기록."""

    def __init__(self, mic_script=None):
        self.mic_script = list(mic_script or [])
        self._speaking = False
        self.calls = []

    async def start(self):
        self.calls.append("start")

    async def close(self):
        self.calls.append("close")

    async def mic_frames(self):
        for blk in self.mic_script:
            yield blk

    async def play_voice(self, pcm, sr):
        self.calls.append(("play_voice", len(pcm), sr))
        self._speaking = True

    def flush_voice(self):
        self.calls.append("flush_voice")
        self._speaking = False

    def is_speaking(self):
        return self._speaking

    async def play_music(self, query):
        self.calls.append(("play_music", query))
        return f"play: {query}"

    async def stop_music(self):
        self.calls.append("stop_music")
        return "stopped"
