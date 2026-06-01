"""마이크 캡처 + VAD. '한 번의 발화(utterance)'를 잘라내 numpy 배열로 돌려준다.

sounddevice 콜백은 별도 스레드에서 돌기 때문에, 오디오 블록을 thread-safe 한
queue 로 asyncio 쪽에 넘긴다. silero-vad 의 VADIterator 로 발화 시작/끝을 잡는다.
"""
import asyncio
import queue

import numpy as np
import sounddevice as sd
from silero_vad import load_silero_vad, VADIterator

import config


class Microphone:
    def __init__(self):
        self._vad_model = load_silero_vad()
        # VADIterator: 프레임을 하나씩 먹이면 발화 시작/끝 이벤트를 돌려준다
        self._vad = VADIterator(
            self._vad_model,
            threshold=config.VAD_THRESHOLD,
            sampling_rate=config.SAMPLE_RATE,
            min_silence_duration_ms=config.SILENCE_MS,
        )
        self._blocks: queue.Queue = queue.Queue()

    def _callback(self, indata, frames, time_info, status):
        # sounddevice 가 오디오 스레드에서 호출. 복사해서 큐에 적재만 한다.
        if status:
            print(f"[audio] {status}")
        self._blocks.put(indata[:, 0].copy())

    async def events(self, wake_detect=None):
        """async generator: 마이크에서 이벤트를 yield.

          ("wake", None)         호출어('Hey Jarvis') 감지 (항상 동작)
          ("start", None)        VAD 가 발화 시작을 감지
          ("utterance", audio)   발화가 끝남 → STT/LLM 처리 대상

        wake_detect(block)->bool: 블록마다 호출되는 호출어 감지 콜백(없으면 생략).
        호출어는 어느 상태에서도 항상 감지되며, VAD 와 독립적으로 동작한다.
        에코 루프 차단·상태 전환은 상위(main 상태머신)가 이벤트로 판단한다.
        """
        loop = asyncio.get_running_loop()
        stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
            blocksize=config.BLOCK_SIZE,
            dtype="float32",
            callback=self._callback,
        )
        with stream:
            collecting = False
            buffer: list[np.ndarray] = []
            while True:
                # 블로킹 큐를 executor 로 비동기 대기 (이벤트 루프 안 막음)
                block = await loop.run_in_executor(None, self._blocks.get)

                # 호출어는 항상 감지 (VAD 와 독립)
                if wake_detect is not None and wake_detect(block):
                    yield ("wake", None)

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
