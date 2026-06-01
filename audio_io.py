"""마이크 캡처 + VAD. '한 번의 발화(utterance)'를 잘라내 numpy 배열로 돌려준다.

AudioBackend 의 mic_frames() async generator 에서 블록을 받아 silero-vad 로
발화 시작/끝을 잡는다.
"""
import asyncio

import numpy as np
from silero_vad import load_silero_vad, VADIterator

import config


class Microphone:
    def __init__(self, backend):
        self.backend = backend
        self._vad_model = load_silero_vad()
        # VADIterator: 프레임을 하나씩 먹이면 발화 시작/끝 이벤트를 돌려준다
        self._vad = VADIterator(
            self._vad_model,
            threshold=config.VAD_THRESHOLD,
            sampling_rate=config.SAMPLE_RATE,
            min_silence_duration_ms=config.SILENCE_MS,
        )

    async def events(self, wake_detect=None, is_speaking=lambda: False):
        """async generator: 마이크에서 이벤트를 yield.

          ("wake", None)         호출어('Hey Jarvis') 감지 (항상 동작)
          ("start", None)        VAD 가 발화 시작을 감지
          ("utterance", audio)   발화가 끝남 → STT/LLM 처리 대상

        wake_detect(block)->bool: 블록마다 호출되는 호출어 감지 콜백(없으면 생략).
          호출어는 어느 상태에서도 항상 감지된다(VAD 와 독립).
        is_speaking()->bool: 자비스가 소리내는 중(효과음/응답 재생)인지.
          True 인 동안엔 VAD 입력을 무시한다 → 삑소리(wake/ok)·TTS 가 마이크로
          되먹임되어 발화로 잡히는 것을 방지. (호출어 감지는 그대로 유지)
        """
        collecting = False
        buffer: list[np.ndarray] = []
        async for block in self.backend.mic_frames():
            # 호출어는 항상 감지 (VAD 와 독립)
            if wake_detect is not None and wake_detect(block):
                # 호출어 직전까지의 캡처·VAD 를 완전히 폐기
                # → 호출어가 명령 캡처로 새어들어가는 것을 방지
                collecting = False
                buffer = []
                self._vad.reset_states()
                yield ("wake", None)
                continue

            # 자비스가 소리내는 중에는 VAD 무시 (효과음/응답 되먹임 차단)
            if is_speaking():
                if collecting:
                    collecting = False
                    buffer = []
                self._vad.reset_states()
                continue

            event = self._vad(block)  # {'start':...} / {'end':...} / None
            if event and "start" in event:
                collecting = True
                buffer = []
                yield ("start", None)
            if collecting:
                buffer.append(block)
            if event and "end" in event and collecting:
                collecting = False
                self._vad.reset_states()
                yield ("utterance", np.concatenate(buffer))
