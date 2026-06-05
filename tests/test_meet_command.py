# tests/test_meet_command.py
import asyncio

import commands


def _run(text, ctx):
    asyncio.run(commands.dispatch(text, ctx))


def test_meet_calls_starter_no_arg():
    called = []
    async def starter(): called.append(True)
    _run("/meet", {"log": lambda *_: None, "start_meeting": starter})
    assert called == [True]


def test_meet_ignores_extra_args():
    called = []
    async def starter(): called.append(True)
    _run("/meet phone", {"log": lambda *_: None, "start_meeting": starter})
    assert called == [True]
