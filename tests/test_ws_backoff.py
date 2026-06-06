import asyncio
from ws_backoff import reconnect_loop


def test_returns_when_stopped_after_success():
    calls = []
    async def run():
        stop = asyncio.Event()
        async def connect_once():
            calls.append(1); stop.set()   # 첫 연결 성공 후 종료 신호
        await reconnect_loop(connect_once, stop, lambda m: None,
                             label="t", init_backoff=0.001, max_backoff=0.002)
    asyncio.run(run())
    assert calls == [1]


def test_retries_and_logs_on_exception():
    calls = []; logs = []
    async def run():
        stop = asyncio.Event()
        async def connect_once():
            calls.append(1)
            if len(calls) >= 3: stop.set()
            raise RuntimeError("boom")
        await reconnect_loop(connect_once, stop, lambda m: logs.append(m),
                             label="t", init_backoff=0.001, max_backoff=0.002)
    asyncio.run(run())
    assert len(calls) == 3
    assert len(logs) == 3
    assert "[t]" in logs[0] and "boom" in logs[0]


def test_cancelled_exits_without_log():
    logs = []
    async def run():
        stop = asyncio.Event()
        async def connect_once():
            raise asyncio.CancelledError()
        await reconnect_loop(connect_once, stop, lambda m: logs.append(m),
                             label="t", init_backoff=0.001, max_backoff=0.002)
    asyncio.run(run())
    assert logs == []


def test_stop_set_before_start_no_connect():
    calls = []
    async def run():
        stop = asyncio.Event(); stop.set()
        async def connect_once(): calls.append(1)
        await reconnect_loop(connect_once, stop, lambda m: None, label="t")
    asyncio.run(run())
    assert calls == []
