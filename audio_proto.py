"""데몬 ↔ Python 프로토콜 프레이밍. 순수 함수(하드웨어/IO 없음)."""
import json
import struct

import numpy as np

# 메시지 타입
MIC = 1          # 데몬→: 16kHz mono float32 마이크 블록
EVENT = 2        # 데몬→: JSON 이벤트
PLAY_VOICE = 3   # →데몬: 48kHz mono float32 TTS/효과음
FLUSH_VOICE = 4  # →데몬: voice 중단+비움
PLAY_MUSIC = 5   # →데몬: 48kHz mono float32 음악
STOP_MUSIC = 6   # →데몬: 음악 중단

_HEADER = struct.Struct("<BI")  # type(1B) + length(4B LE)


def encode(mtype: int, payload: bytes = b"") -> bytes:
    return _HEADER.pack(mtype, len(payload)) + payload


def encode_pcm(mtype: int, samples: np.ndarray) -> bytes:
    b = np.ascontiguousarray(samples, dtype="<f4").tobytes()
    return encode(mtype, b)


def encode_event(obj) -> bytes:
    return encode(EVENT, json.dumps(obj).encode("utf-8"))


def decode_event(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


def pcm_to_array(payload: bytes) -> np.ndarray:
    return np.frombuffer(payload, dtype="<f4")


class FrameDecoder:
    """바이트를 feed() 하고 iterate 하면 완성된 (type, payload) 프레임을 yield."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data

    def __iter__(self):
        while len(self.buf) >= _HEADER.size:
            mtype, length = _HEADER.unpack_from(self.buf, 0)
            if len(self.buf) < _HEADER.size + length:
                return
            payload = bytes(self.buf[_HEADER.size:_HEADER.size + length])
            del self.buf[:_HEADER.size + length]
            yield mtype, payload
