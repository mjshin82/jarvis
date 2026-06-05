# tests/test_mic_command.py
import asyncio

import commands


class FakeRouter:
    def __init__(self):
        self.mode = None

    def set_override(self, mode):
        self.mode = mode


def _run(text, ctx):
    asyncio.run(commands.dispatch(text, ctx))


def test_mic_phone_switches_source():
    router = FakeRouter()
    logs = []
    _run("/mic phone", {"log": logs.append, "mic_router": router})
    assert router.mode == "remote"


def test_mic_system_switches_source():
    router = FakeRouter()
    _run("/mic system", {"log": lambda *_: None, "mic_router": router})
    assert router.mode == "local"


def test_mic_auto_switches_source():
    router = FakeRouter()
    _run("/mic auto", {"log": lambda *_: None, "mic_router": router})
    assert router.mode == "auto"


def test_mic_no_arg_triggers_wake():
    called = []

    async def trig():
        called.append(True)

    ctx = {"log": lambda *_: None, "trigger_wake": trig, "mic_router": FakeRouter()}
    _run("/mic", ctx)
    assert called == [True]


def test_mic_phone_without_router_warns():
    logs = []
    _run("/mic phone", {"log": logs.append, "mic_router": None})
    assert any("비활성" in m for m in logs)
