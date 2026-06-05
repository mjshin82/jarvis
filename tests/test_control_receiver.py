# tests/test_control_receiver.py
import asyncio
from control_receiver import ControlReceiver


def _rx(calls):
    async def on_command(kind):
        calls.append(kind)
    return ControlReceiver("ws://x", "tok", on_command=on_command,
                           on_log=lambda *a: None, key="k")


def test_meeting_stop_dispatches():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"meeting_stop"}'))
    assert calls == ["meeting_stop"]


def test_other_kinds_ignored():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"something_else"}'))
    asyncio.run(rx._handle_message("not json at all"))
    asyncio.run(rx._handle_message('{"no":"kind"}'))
    assert calls == []
