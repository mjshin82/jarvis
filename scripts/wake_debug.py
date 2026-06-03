"""Wake word 라이브 점수 디버거.
실행: .venv/bin/python scripts/wake_debug.py
'Hey Jarvis' 라고 말했을 때 점수가 0.4(WAKE_THRESHOLD)를 넘는지 확인.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import sounddevice as sd
import openwakeword, openwakeword.utils
from openwakeword.model import Model
import config

print("[wake-debug] 입력 장치 목록:")
print(sd.query_devices())
print(f"[wake-debug] 기본 입력: {sd.query_devices(kind='input')['name']}")
print(f"[wake-debug] threshold={config.WAKE_THRESHOLD}, model={config.WAKE_MODEL}")

# audio_io 와 동일한 자동 선택 (BlackHole 회피)
def _pick_device():
    spec = config.MIC_DEVICE.strip()
    if spec:
        if spec.isdigit(): return int(spec)
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and spec.lower() in d["name"].lower():
                return i
    skip = ("blackhole", "loopback", "aggregate", "teams", "soundflower")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and not any(s in d["name"].lower() for s in skip):
            return i
    return None

DEVICE = _pick_device()
if DEVICE is not None:
    print(f"[wake-debug] 선택된 장치: [{DEVICE}] {sd.query_devices(DEVICE)['name']}")

try:
    openwakeword.utils.download_models([config.WAKE_MODEL])
except Exception as e:
    print(f"download skip: {e}")

model = Model(wakeword_models=[config.WAKE_MODEL], inference_framework="onnx")
key = config.WAKE_MODEL

CHUNK = 1280  # 80ms @ 16kHz
buf = np.zeros(0, dtype=np.int16)
peak_level = 0.0
peak_score = 0.0
last_print = time.time()

def cb(indata, frames, t, status):
    global buf, peak_level, peak_score, last_print
    if status:
        print(f"[status] {status}")
    block = indata[:, 0]
    peak_level = max(peak_level, float(np.max(np.abs(block))))
    pcm16 = (np.clip(block, -1.0, 1.0) * 32767).astype(np.int16)
    buf = np.concatenate([buf, pcm16])
    while len(buf) >= CHUNK:
        chunk = buf[:CHUNK]; buf = buf[CHUNK:]
        scores = model.predict(chunk)
        s = scores.get(key, 0.0)
        peak_score = max(peak_score, s)
        if s >= config.WAKE_THRESHOLD:
            print(f"🎯 DETECT score={s:.3f}")
    now = time.time()
    if now - last_print >= 0.5:
        bar = "#" * int(peak_score * 40)
        print(f"mic_peak={peak_level:.2f}  wake_peak={peak_score:.3f} |{bar:<40}|")
        peak_level = 0.0; peak_score = 0.0; last_print = now

print("\n>>> 5초 후 시작. 'Hey Jarvis' 를 또렷하게 여러 번 시도하세요. Ctrl+C 종료.\n")
time.sleep(1)
with sd.InputStream(
    samplerate=config.SAMPLE_RATE, channels=config.CHANNELS,
    blocksize=config.BLOCK_SIZE, dtype="float32", callback=cb,
    device=DEVICE,
):
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n종료")
