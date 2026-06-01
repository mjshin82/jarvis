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

    async def events(self, is_speaking=lambda: False, half_duplex=True):
        """async generator: VAD 이벤트를 yield.

          ("start", None)        사용자가 말을 시작함  → barge-in 트리거
          ("utterance", audio)   발화가 끝남          → STT/LLM 처리 대상

        is_speaking(): 자비스가 현재 말하는 중인지 알려주는 콜백.
        half_duplex:
          True(기본, 스피커 사용):  자비스가 말하는 동안 마이크 입력을 통째로 무시한다.
              → 스피커 소리가 마이크로 되먹임되어 STT→LLM 으로 무한 호출되는
                에코 루프를 원천 차단. 대신 재생 중 barge-in 은 불가.
                (wake word 층을 얹으면 호출어로 끼어들 수 있다 — 다음 단계)
          False(헤드폰 사용): 에코 경로가 없으므로 재생 중에도 즉시 barge-in 허용.
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

                # --- 반이중: 자비스가 말하는 동안엔 마이크 무시 (에코 루프 차단) ---
                if half_duplex and is_speaking():
                    if collecting:                 # 진행 중이던 수집은 폐기
                        collecting = False
                        buffer = []
                    self._vad.reset_states()        # VAD 상태 리셋 → 재생 끝나면 새로 시작
                    continue

                event = self._vad(block)  # {'start':...} / {'end':...} / None

                if event and "start" in event:
                    collecting = True
                    buffer = []
                    yield ("start", None)           # open 모드에선 즉시 barge-in 트리거
                if collecting:
                    buffer.append(block)
                if event and "end" in event and collecting:
                    collecting = False
                    self._vad.reset_states()
                    yield ("utterance", np.concatenate(buffer))
