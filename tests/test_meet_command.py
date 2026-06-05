# tests/test_meet_command.py
import asyncio

import commands


def _run(text, ctx):
    asyncio.run(commands.dispatch(text, ctx))


def test_meet_phone_passes_use_remote_true():
    got = {}
    async def starter(use_remote): got["v"] = use_remote
    _run("/meet phone", {"log": lambda *_: None, "start_meeting": starter})
    assert got["v"] is True


def test_meet_system_and_noarg_false():
    got = []
    async def starter(use_remote): got.append(use_remote)
    _run("/meet system", {"log": lambda *_: None, "start_meeting": starter})
    _run("/meet", {"log": lambda *_: None, "start_meeting": starter})
    assert got == [False, False]
