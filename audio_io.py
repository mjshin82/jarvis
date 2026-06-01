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

    async def events(self, is_speaking=lambda: False):
        """async generator: VAD 이벤트를 yield. 에코 완화 게이트 포함.

          ("start", None)        사용자가 말을 시작함  → barge-in 트리거
          ("utterance", audio)   발화가 끝남          → STT/LLM 처리 대상

        is_speaking(): 자비스가 현재 말하는 중인지 알려주는 콜백.
          - 재생 중이면 VAD 임계값을 높여(VAD_THRESHOLD_SPEAKING) 약한 에코를 무시
          - 재생 중엔 발화가 BARGE_IN_MIN_MS 이상 지속될 때만 'start' 를 알림.
            지속시간 미달 구간은 에코로 보고 'utterance' 로도 내보내지 않는다.
        """
        loop = asyncio.get_running_loop()
        ms_per_block = config.BLOCK_SIZE / config.SAMPLE_RATE * 1000
        min_blocks = max(1, round(config.BARGE_IN_MIN_MS / ms_per_block))

        stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
            blocksize=config.BLOCK_SIZE,
            dtype="float32",
            callback=self._callback,
        )
        with stream:
            collecting = False
            announced = False            # 이번 발화의 'start' 를 이미 알렸는지
            buffer: list[np.ndarray] = []
            while True:
                # 블로킹 큐를 executor 로 비동기 대기 (이벤트 루프 안 막음)
                block = await loop.run_in_executor(None, self._blocks.get)

                # 재생 중이면 더 엄격한 임계값 적용 (에코 억제)
                speaking = is_speaking()
                self._vad.threshold = (
                    config.VAD_THRESHOLD_SPEAKING if speaking else config.VAD_THRESHOLD
                )
                event = self._vad(block)  # {'start':...} / {'end':...} / None

                if event and "start" in event:
                    collecting = True
                    announced = False
                    buffer = []
                if collecting:
                    buffer.append(block)
                    # barge-in 알림 게이트: 재생 중이면 최소 지속시간 충족 후에만 알림
                    if not announced and (not speaking or len(buffer) >= min_blocks):
                        announced = True
                        yield ("start", None)
                if event and "end" in event and collecting:
                    collecting = False
                    self._vad.reset_states()
                    # announced 된 발화만 처리. (재생 중 짧은 에코 blip 은 버림)
                    if announced:
                        yield ("utterance", np.concatenate(buffer))
