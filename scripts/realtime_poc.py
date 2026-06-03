"""RealtimeSTT PoC — Mac 에서 실시간 자막 STT 가 어느 정도 도는지 단독 검증.

실행: .venv/bin/python scripts/realtime_poc.py
종료: Ctrl+C

화면 출력:
  [실시간]  진행 중인 부분 결과 (계속 갱신, tiny 모델로 빠르게)
  [확정]    발화 끝나면 정확하게 다시 한 번 (small 모델)

자비스에 통합하기 전 다음만 확인:
  1. 첫 토큰 표시까지 지연 (체감)
  2. CPU 부하 (Activity Monitor 로)
  3. 인식 정확도 (실시간 vs 확정 차이)
  4. 화면 깜빡임 정도
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from RealtimeSTT import AudioToTextRecorder


def on_realtime(text: str):
    """부분 결과가 갱신될 때마다 호출. 같은 줄에 덮어쓰기."""
    sys.stdout.write(f"\r[실시간] {text:<100}")
    sys.stdout.flush()


def on_final(text: str):
    """발화가 끝나면 호출. 최종 확정 텍스트."""
    sys.stdout.write(f"\r[확정 ] {text}\n")
    sys.stdout.flush()


def pick_physical_mic():
    """BlackHole/Teams 같은 가상 장치를 피해서 첫 번째 물리 마이크 인덱스 반환."""
    import pyaudio
    p = pyaudio.PyAudio()
    skip = ("blackhole", "loopback", "aggregate", "teams", "soundflower")
    chosen = None
    try:
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] <= 0:
                continue
            name = info["name"].lower()
            if any(s in name for s in skip):
                continue
            chosen = i
            print(f"[mic] 입력 장치 선택: [{i}] {info['name']}")
            break
    finally:
        p.terminate()
    return chosen


def main():
    print("=== RealtimeSTT PoC ===")
    print("  실시간 모델: tiny")
    print("  최종 모델  : small")
    print("  언어       : 자동 감지")
    mic_idx = pick_physical_mic()
    print("  말해보세요. Ctrl+C 종료.\n")

    recorder = AudioToTextRecorder(
        model="small",
        realtime_model_type="tiny",
        enable_realtime_transcription=True,
        on_realtime_transcription_update=on_realtime,
        on_recording_start=lambda: print("\n[event] recording_start"),
        on_recording_stop=lambda: print("\n[event] recording_stop"),
        on_vad_detect_start=lambda: print("\n[event] vad_detect_start (말 시작 감지)"),
        on_vad_detect_stop=lambda: print("\n[event] vad_detect_stop (말 끝 감지)"),
        language="",
        spinner=False,
        post_speech_silence_duration=0.6,
        silero_sensitivity=0.4,
        webrtc_sensitivity=3,
        device="cpu",
        compute_type="int8",
        input_device_index=mic_idx,          # BlackHole 회피
        level=20,                            # logging.INFO (DEBUG는 너무 시끄러움)
    )

    try:
        while True:
            recorder.text(on_final)
    except KeyboardInterrupt:
        print("\n종료.")
    finally:
        recorder.shutdown()


if __name__ == "__main__":
    main()
