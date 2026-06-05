# tests/test_control_receiver.py
import asyncio
from control_receiver import ControlReceiver


def _rx(calls):
    async def on_command(msg):
        calls.append(msg)
    return ControlReceiver("ws://x", "tok", on_command=on_command,
                           on_log=lambda *a: None, key="k")


def test_dispatch_passes_full_msg():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"meeting_stop"}'))
    assert calls == [{"kind": "meeting_stop"}]


def test_dispatch_with_payload():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"apply_settings","value":{"translate_backend":"local"}}'))
    assert calls == [{"kind": "apply_settings", "value": {"translate_backend": "local"}}]


def test_non_commands_ignored():
    calls = []
    rx = _rx(calls)
    asyncio.run(rx._handle_message('{"kind":"no_receiver"}'))   # 로그만
    asyncio.run(rx._handle_message("not json at all"))
    asyncio.run(rx._handle_message('{"no":"kind"}'))
    assert calls == []
