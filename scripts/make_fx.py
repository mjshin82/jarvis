"""기본 효과음 생성: sound/fx/wake.wav, sound/fx/ok.wav
원하는 소리가 있으면 같은 경로의 wav 로 교체하면 된다.
실행: python scripts/make_fx.py
"""
import os

import numpy as np
import soundfile as sf

SR = 44_100
OUT = "sound/fx"


def tone(freq, dur, sr=SR, vol=0.3):
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    wave = np.sin(2 * np.pi * freq * t).astype(np.float32)
    # 클릭 방지용 페이드 인/아웃 (앞뒤 8ms)
    n = max(1, int(sr * 0.008))
    env = np.ones_like(wave)
    env[:n] = np.linspace(0, 1, n)
    env[-n:] = np.linspace(1, 0, n)
    return wave * env * vol


def main():
    os.makedirs(OUT, exist_ok=True)
    gap = np.zeros(int(SR * 0.04), dtype=np.float32)

    # wake: 상승 2음 "바-딩" → "듣고 있어요"
    wake = np.concatenate([tone(660, 0.10), gap, tone(990, 0.14)])
    sf.write(f"{OUT}/wake.wav", wake, SR)

    # ok: 짧은 단음 "삑" → "접수"
    ok = tone(880, 0.12)
    sf.write(f"{OUT}/ok.wav", ok, SR)

    print(f"생성: {OUT}/wake.wav ({len(wake)/SR:.2f}s), {OUT}/ok.wav ({len(ok)/SR:.2f}s)")


if __name__ == "__main__":
    main()
