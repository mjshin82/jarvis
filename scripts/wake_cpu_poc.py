"""RealtimeSTT 호출어 감지 + CPU 사용량 검증 PoC.

자비스 본체를 RealtimeSTT 기반으로 교체할지 결정하기 위한 측정 스크립트.

측정 항목
  1) 'Hey Jarvis' 호출어 감지율 — 정확도 / 오감지 / 응답 지연
  2) CPU 사용량 — 침묵 중 / 발화 중 / 호출어 대기 중

사용법
  .venv/bin/python scripts/wake_cpu_poc.py

  - 시작하면 'Hey Jarvis' 라고 10번 정도 또렷이 불러본다.
  - 발음/거리/볼륨을 살짝씩 바꿔보면서 어디까지 잡히는지 본다.
  - 그 사이사이 침묵 구간을 두면 idle CPU 측정도 자동.
  - 일반 대화도 섞어 오감지(false trigger) 빈도를 본다.
  - Ctrl+C 로 종료 → 요약 통계 출력.
"""
import os
import sys
import time
import statistics
import threading
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil
from RealtimeSTT import AudioToTextRecorder


@dataclass
class Stats:
    wakes: int = 0
    wake_times: list = field(default_factory=list)         # wake 감지 시각
    utterances: list = field(default_factory=list)         # (시각, 텍스트)
    cpu_samples_idle: list = field(default_factory=list)   # 침묵 중 CPU%
    cpu_samples_busy: list = field(default_factory=list)   # 발화/처리 중 CPU%


STATS = Stats()
STATE = {"recording": False, "wake_active": False, "last_wake_ts": 0.0,
         "first_audio_ts": None, "stop": False}


def pick_physical_mic() -> int | None:
    import pyaudio
    p = pyaudio.PyAudio()
    skip = ("blackhole", "loopback", "aggregate", "teams", "soundflower")
    chosen = None
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] <= 0:
                continue
            if any(s in info["name"].lower() for s in skip):
                continue
            chosen = i
            print(f"[mic] 입력 장치: [{i}] {info['name']}")
            break
    finally:
        p.terminate()
    return chosen


def cpu_sampler():
    """0.5초마다 현재 Python 프로세스 CPU% 를 STATE 에 맞춰 분류."""
    proc = psutil.Process(os.getpid())
    # 첫 호출은 0 나옴 — 워밍업
    proc.cpu_percent(interval=None)
    while not STATE["stop"]:
        time.sleep(0.5)
        pct = proc.cpu_percent(interval=None)
        if STATE["recording"]:
            STATS.cpu_samples_busy.append(pct)
        else:
            STATS.cpu_samples_idle.append(pct)


def on_wakeword():
    """RealtimeSTT 가 'Hey Jarvis' 를 감지한 순간."""
    t = time.time()
    STATS.wakes += 1
    STATS.wake_times.append(t)
    STATE["last_wake_ts"] = t
    STATE["wake_active"] = True
    print(f"\n🎯 WAKE #{STATS.wakes}  (총 {STATS.wakes}회)")


def on_recording_start():
    STATE["recording"] = True
    STATE["first_audio_ts"] = time.time()


def on_recording_stop():
    STATE["recording"] = False


def on_realtime(text: str):
    sys.stdout.write(f"\r[실시간] {text:<80}")
    sys.stdout.flush()


def on_final(text: str):
    t = time.time()
    STATS.utterances.append((t, text))
    sys.stdout.write(f"\r[확정 ] {text}\n")
    sys.stdout.flush()
    STATE["wake_active"] = False


def print_summary():
    print("\n\n=" * 1, "=" * 60, sep="")
    print("📊 측정 결과 요약")
    print("=" * 62)

    # 호출어
    print(f"\n[호출어 'Hey Jarvis']")
    print(f"  감지 횟수      : {STATS.wakes}")
    print(f"  발화로 이어진 수: {len(STATS.utterances)}")
    if STATS.wakes:
        unanswered = STATS.wakes - len(STATS.utterances)
        print(f"  발화 없이 종료  : {unanswered}회 (timeout 또는 잘못 감지)")

    # CPU
    print(f"\n[CPU 사용량 (현재 프로세스, %)]")
    def stat(samples, label):
        if not samples:
            print(f"  {label:8}: (샘플 없음)")
            return
        print(f"  {label:8}: 평균 {statistics.mean(samples):5.1f}% / "
              f"중간 {statistics.median(samples):5.1f}% / "
              f"최대 {max(samples):5.1f}% / 샘플 {len(samples)}개")
    stat(STATS.cpu_samples_idle, "침묵 중")
    stat(STATS.cpu_samples_busy, "녹음 중")

    # 메모리
    proc = psutil.Process(os.getpid())
    mem_mb = proc.memory_info().rss / (1024 * 1024)
    print(f"\n[메모리]")
    print(f"  RSS: {mem_mb:.0f} MB")

    print("\n해석 가이드")
    print("  - 감지 횟수가 실제 부른 횟수보다 많으면 → 오감지(false trigger) 많음.")
    print("    임계값(wake_words_sensitivity)을 낮춰야 함.")
    print("  - 감지 횟수가 적으면 → 민감도 올리거나 발음/거리/볼륨 조정.")
    print("  - 침묵 중 CPU 가 10%+ 면 상시 부하 큼. tiny 모델이라도 무시 못 함.")
    print("=" * 62)


def main():
    print("=" * 62)
    print("RealtimeSTT 호출어 + CPU PoC")
    print("=" * 62)
    print("측정:")
    print("  - 'Hey Jarvis' 라고 10번 정도 부르세요 (발음/거리/볼륨 다양하게)")
    print("  - 사이사이 침묵을 두면 idle CPU 도 측정됩니다")
    print("  - 일반 대화도 섞어보면 오감지 빈도를 알 수 있어요")
    print("  - Ctrl+C 로 종료 → 통계 출력")
    print("=" * 62)

    mic_idx = pick_physical_mic()

    # CPU 샘플러 시작 (백그라운드)
    sampler = threading.Thread(target=cpu_sampler, daemon=True)
    sampler.start()

    recorder = AudioToTextRecorder(
        model="tiny",                            # 메인도 tiny 로 (검증 목적)
        realtime_model_type="tiny",
        enable_realtime_transcription=True,
        on_realtime_transcription_update=on_realtime,
        on_wakeword_detected=on_wakeword,
        on_recording_start=on_recording_start,
        on_recording_stop=on_recording_stop,
        wakeword_backend="oww",                  # openwakeword
        wake_words="hey_jarvis",                 # 자비스가 지금 쓰는 모델과 동일
        wake_words_sensitivity=0.6,              # 기본값
        wake_word_timeout=5.0,
        language="",
        spinner=False,
        post_speech_silence_duration=0.7,
        silero_sensitivity=0.4,
        webrtc_sensitivity=3,
        device="cpu",
        compute_type="int8",
        input_device_index=mic_idx,
        level=30,                                # WARNING 만 표시 (조용히)
    )

    print(f"\n[설정] wake='hey_jarvis' / sensitivity=0.6 / model=tiny")
    print("[준비 완료] 'Hey Jarvis' 라고 불러보세요.\n")

    start_time = time.time()
    try:
        while True:
            recorder.text(on_final)
    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        print(f"\n총 시간: {elapsed:.1f}초")
    finally:
        STATE["stop"] = True
        recorder.shutdown()
        print_summary()


if __name__ == "__main__":
    main()
