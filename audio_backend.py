"""오디오 입출력 추상화. 구현: SounddeviceBackend(폴백), AECBackend(Swift 데몬)."""
import abc

import numpy as np


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
