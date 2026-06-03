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

    def _resolve_device(self):
        """MIC_DEVICE 환경변수 우선. 비었으면 입력 채널 있는 첫 번째 물리 마이크 자동 선택
        (BlackHole 같은 가상장치 회피)."""
        spec = config.MIC_DEVICE.strip()
        if spec:
            if spec.isdigit():
                return int(spec)
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and spec.lower() in d["name"].lower():
                    return i
            print(f"[audio] MIC_DEVICE='{spec}' 매칭 실패 → 기본 장치 사용")
            return None
        # 자동: BlackHole/Loopback/Aggregate 같은 가상장치는 건너뛰기
        skip = ("blackhole", "loopback", "aggregate", "teams", "soundflower")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0:
                continue
            if any(s in d["name"].lower() for s in skip):
                continue
            return i
        return None

    def _callback(self, indata, frames, time_info, status):
        # sounddevice 가 오디오 스레드에서 호출. 복사해서 큐에 적재만 한다.
        if status:
            print(f"[audio] {status}")
        self._blocks.put(indata[:, 0].copy())

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
        loop = asyncio.get_running_loop()
        device = self._resolve_device()
        if device is not None:
            info = sd.query_devices(device)
            print(f"[audio] 입력 장치: [{device}] {info['name']}")
        stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
            blocksize=config.BLOCK_SIZE,
            dtype="float32",
            callback=self._callback,
            device=device,
        )
        with stream:
            collecting = False
            buffer: list[np.ndarray] = []
            while True:
                # 블로킹 큐를 executor 로 비동기 대기 (이벤트 루프 안 막음)
                block = await loop.run_in_executor(None, self._blocks.get)

                # 호출어는 항상 감지 (VAD 와 독립)
                if wake_detect is not None and wake_detect(block):
                    # 호출어 직전까지의 캡처·VAD·큐 잔여(= 'Hey Jarvis' 음성)를 완전히 폐기
                    # → 호출어가 명령 캡처로 새어들어가는 것을 방지
                    collecting = False
                    buffer = []
                    self._vad.reset_states()
                    while not self._blocks.empty():
                        try:
                            self._blocks.get_nowait()
                        except queue.Empty:
                            break
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
