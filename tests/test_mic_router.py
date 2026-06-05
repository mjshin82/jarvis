# tests/test_mic_router.py
import queue

import numpy as np

import config
from mic_source import MicRouter


class _FakeRemote:
    def reset(self):
        pass


class _FakeLocal:
    def start(self):
        pass

    def stop(self):
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


def test_pause_local_suppresses_remote_frames():
    q = queue.Queue()

    class _Rem:
        def __init__(self):
            self.fed = 0
        def feed(self, b):
            self.fed += 1
        def reset(self):
            pass

    rem = _Rem()
    r = MicRouter(q, local=_FakeLocal(), remote=rem)
    r.pause_local()
    r.on_remote_frame(b"\x00\x00")   # 무시되어야
    assert rem.fed == 0
    r.resume_local()
    r.on_remote_frame(b"\x00\x00")   # 이제 처리
    assert rem.fed == 1


def test_on_switch_called_with_new_source():
    q = queue.Queue()
    seen = []
    r = MicRouter(q, local=_FakeLocal(), remote=_FakeRemote())
    r.on_switch = seen.append
    r.note_remote_activity(now=1.0)        # local→remote
    r.set_override("local")                # remote→local
    r.set_override("local")                # 변화 없음 → 콜백 없음
    assert seen == ["remote", "local"]


def test_tap_diverts_remote_frames_and_bypasses_queue():
    q = queue.Queue()
    tapped = []
    fed = []

    class Rem:
        def feed(self, b): fed.append(b)
        def reset(self): pass

    r = MicRouter(q, local=_FakeLocal(), remote=Rem())
    r.set_override("remote")          # tap 없으면 큐로 갈 상황
    r.set_tap(tapped.append)
    r.on_remote_frame(b"\x01\x02")
    assert tapped == [b"\x01\x02"]    # tap 으로 우회
    assert fed == []                  # remote.feed 안 탐
    assert q.empty()                  # 메인 큐 미적재

    r.set_tap(None)                   # 해제 → 기존 경로 복귀
    r.on_remote_frame(b"\x03\x04")
    assert tapped == [b"\x01\x02"]    # tap 은 더 안 늘어남
    assert fed == [b"\x03\x04"]       # remote.feed 로 감


def test_active_property():
    q = queue.Queue()
    r = MicRouter(q, local=_FakeLocal(), remote=_FakeRemote())
    assert r.active == "local"
    r.set_override("remote")
    assert r.active == "remote"
