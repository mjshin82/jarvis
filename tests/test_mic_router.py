# tests/test_mic_router.py
import queue

import numpy as np

import config
from mic_source import MicRouter


class _FakeRemote:
    def reset(self):
        pass


def _block(v=0.0):
    return np.full(512, v, dtype=np.float32)


def test_only_active_source_reaches_queue():
    q = queue.Queue()
    r = MicRouter(q, local=object(), remote=object())   # 소스는 안 씀(게이팅만 검증)
    # 기본 active=local
    r._sink_remote(_block(0.1))
    assert q.empty()
    r._sink_local(_block(0.2))
    assert q.qsize() == 1


def test_auto_switches_to_remote_on_activity_and_back_on_idle():
    q = queue.Queue()
    r = MicRouter(q, local=object(), remote=_FakeRemote())
    r.note_remote_activity(now=100.0)
    assert r._active == "remote"
    r._sink_remote(_block())
    assert q.qsize() == 1
    # idle 미만 → 유지
    r.check_idle(now=100.0 + config.REMOTE_MIC_IDLE_S - 0.1)
    assert r._active == "remote"
    # idle 초과 → local 복귀 + 큐 비움
    r.check_idle(now=100.0 + config.REMOTE_MIC_IDLE_S + 0.1)
    assert r._active == "local"
    assert q.empty()


def test_manual_override_beats_auto():
    q = queue.Queue()
    r = MicRouter(q, local=object(), remote=_FakeRemote())
    r.set_override("remote")
    r.note_remote_activity(now=0.0)
    r.check_idle(now=10_000.0)   # auto 가 아니므로 복귀 안 함
    assert r._active == "remote"
    r.set_override("local")
    r.note_remote_activity(now=10_001.0)   # 무시됨
    assert r._active == "local"
