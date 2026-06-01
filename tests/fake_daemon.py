"""stdin 프레임을 읽고, MIC 프레임 몇 개와 drained 이벤트를 stdout 으로 보낸다."""
import sys
import os
# 프로젝트 루트를 sys.path 에 추가 (서브프로세스로 실행 시 임포트 경로 보장)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import audio_proto as p

def main():
    out = sys.stdout.buffer
    for _ in range(3):
        out.write(p.encode_pcm(p.MIC, np.zeros(512, np.float32)))
    out.flush()
    dec = p.FrameDecoder()
    while True:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            break
        dec.feed(chunk)
        for mtype, _payload in dec:
            if mtype == p.PLAY_VOICE:
                out.write(p.encode_event({"voice": "drained"})); out.flush()

if __name__ == "__main__":
    main()
