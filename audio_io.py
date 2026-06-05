"""마이크 캡처 + VAD. '한 번의 발화(utterance)'를 잘라내 numpy 배열로 돌려준다.

sounddevice 콜백은 별도 스레드에서 돌기 때문에, 오디오 블록을 thread-safe 한
queue 로 asyncio 쪽에 넘긴다. silero-vad 의 VADIterator 로 발화 시작/끝을 잡는다.
"""
import asyncio
import queue

import numpy as np
from silero_vad import load_silero_vad, VADIterator

import config
from simulation import MODE


class Microphone:
    """마이크 입력 + VAD + wake.
    pause()/resume() 으로 시스템 마이크 stream 을 닫고 다시 열 수 있다 — 다른 STT
    라이브러리(예: RealtimeSTT) 가 마이크를 점유해야 하는 회의 모드 진입 시 사용."""

    def __init__(self, *, vad_default=None, vad_translate=None):
        self._paused = False
        self._blocks: queue.Queue = queue.Queue()
        from mic_source import MicRouter
        self.router = MicRouter(self._blocks)
        # VAD 주입(테스트) 또는 기본 생성. 모드별 침묵 임계가 다르다 — 평상시는 빠른
        # 응답, 번역은 긴 문장 묶음. VADIterator 는 init 후 임계 변경 불가라 두 개를 든다.
        if vad_default is None or vad_translate is None:
            self._vad_model = load_silero_vad()
        if vad_default is None:
            vad_default = VADIterator(
                self._vad_model, threshold=config.VAD_THRESHOLD,
                sampling_rate=config.SAMPLE_RATE,
                min_silence_duration_ms=config.SILENCE_MS,
            )
        if vad_translate is None:
            vad_translate = VADIterator(
                self._vad_model, threshold=config.VAD_THRESHOLD,
                sampling_rate=config.SAMPLE_RATE,
                min_silence_duration_ms=config.SILENCE_MS_TRANSLATE,
            )
        self._vad_default = vad_default
        self._vad_translate = vad_translate
        self._vad = self._vad_default

    def _pick_vad(self):
        """현재 모드에 맞는 VAD 인스턴스를 돌려준다.
        호출 시점에 결정 — 발화 진행 중에는 바꾸지 않는다(컨텍스트 깨짐)."""
        return self._vad_translate if MODE.is_translate() else self._vad_default

    def pause(self) -> None:
        """시스템 마이크 stream 을 닫는다 → 다른 라이브러리(회의 모드 RealtimeSTT 등)가
        장치를 점유 가능. events() 루프는 큐에 블록이 안 와 자연히 멈춰 있다가 resume() 후 재개."""
        self._paused = True
        self.router.pause_local()

    def resume(self) -> None:
        """시스템 마이크 stream 을 다시 연다."""
        self.router.resume_local()
        self._paused = False

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
        self.router.start()
        # 발화 시작 *직전* 의 짧은 audio 도 잡아두면 첫 음절 잘림이 줄어든다.
        pre_roll_max = 6
        pre_roll: list[np.ndarray] = []
        try:
            collecting = False
            buffer: list[np.ndarray] = []
            while True:
                # 일시정지 중에는 큐에서 의미있는 블록이 안 들어옴.
                # 잠깐씩 양보하면서 재개 신호 기다림.
                if self._paused:
                    await asyncio.sleep(0.1)
                    # 잔여 블록 비움(재개 직후 옛 audio 사용 방지)
                    while not self._blocks.empty():
                        try:
                            self._blocks.get_nowait()
                        except queue.Empty:
                            break
                    collecting = False
                    buffer = []
                    pre_roll = []
                    self._vad_default.reset_states()
                    self._vad_translate.reset_states()
                    continue
                # 블로킹 큐를 executor 로 비동기 대기 (이벤트 루프 안 막음)
                block = await loop.run_in_executor(None, self._blocks.get)

                # 호출어는 항상 감지 (VAD 와 독립)
                if wake_detect is not None and wake_detect(block):
                    # 호출어 직전까지의 캡처·VAD·큐 잔여(= 'Hey Jarvis' 음성)를 완전히 폐기
                    # → 호출어가 명령 캡처로 새어들어가는 것을 방지
                    collecting = False
                    buffer = []
                    pre_roll = []
                    self._vad_default.reset_states()
                    self._vad_translate.reset_states()
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
                    pre_roll = []
                    self._vad_default.reset_states()
                    self._vad_translate.reset_states()
                    continue

                # 발화 진행 중이 아니라면 모드에 맞는 VAD 를 매번 골라준다
                if not collecting:
                    self._vad = self._pick_vad()

                event = self._vad(block)  # {'start':...} / {'end':...} / None
                if event and "start" in event:
                    collecting = True
                    # pre_roll 을 버퍼 시작에 끼워 첫 음절 잘림 완화
                    buffer = list(pre_roll)
                    yield ("start", None)
                if collecting:
                    buffer.append(block)
                else:
                    # 항상 최근 audio 를 짧게 유지(다음 발화의 pre_roll 용)
                    pre_roll.append(block)
                    if len(pre_roll) > pre_roll_max:
                        pre_roll.pop(0)
                if event and "end" in event and collecting:
                    collecting = False
                    self._vad.reset_states()
                    yield ("utterance", np.concatenate(buffer))
                    pre_roll = []   # 발화 종료 후 새로 채워나감
        finally:
            self.router.stop()
