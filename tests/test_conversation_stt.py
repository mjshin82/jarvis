import asyncio
from conversation_stt import ConversationSTT


class FakeBackend:
    def __init__(self, name): self.name = name; self.started = 0; self.closed = 0; self.fed = []
    async def start(self): self.started += 1
    async def close(self): self.closed += 1
    def feed_block(self, b): self.fed.append(b)


def _facade(backend="local"):
    state = {"backend": backend}
    local = FakeBackend("local")
    gladias = []
    def make_local(): return local
    def make_gladia():
        g = FakeBackend("gladia"); gladias.append(g); return g
    f = ConversationSTT(make_local=make_local, make_gladia=make_gladia,
                        settings_get=lambda k: state["backend"], on_log=lambda *a: None)
    return f, local, gladias, state


def test_resume_local_uses_local_and_routes_feed():
    async def run():
        f, local, gladias, _ = _facade("local")
        await f.resume()
        f.feed_block(b"x")
        assert local.started == 1 and local.fed == [b"x"] and gladias == []
    asyncio.run(run())


def test_resume_gladia_creates_and_starts_gladia():
    async def run():
        f, local, gladias, _ = _facade("gladia")
        await f.resume()
        f.feed_block(b"y")
        assert len(gladias) == 1 and gladias[0].started == 1 and gladias[0].fed == [b"y"]
        assert local.started == 0
    asyncio.run(run())


def test_suspend_closes_gladia_but_keeps_local():
    async def run():
        f, local, gladias, state = _facade("gladia")
        await f.resume(); await f.suspend()
        assert gladias[0].closed == 1
        state["backend"] = "local"
        await f.resume(); await f.suspend()
        assert local.closed == 0   # 로컬은 상시 유지
    asyncio.run(run())


def test_live_switch_local_to_gladia_keeps_local_open():
    async def run():
        f, local, gladias, state = _facade("local")
        await f.resume()                 # local active
        state["backend"] = "gladia"
        await f.resume()                 # gladia active, local 유지
        assert len(gladias) == 1 and gladias[0].started == 1
        assert local.closed == 0
    asyncio.run(run())


def test_switch_gladia_to_local_closes_gladia():
    async def run():
        f, local, gladias, state = _facade("gladia")
        await f.resume()                 # gladia active
        state["backend"] = "local"
        await f.resume()                 # local active → gladia 닫힘
        assert gladias[0].closed == 1 and local.started == 1
    asyncio.run(run())


def test_feed_block_noop_without_active():
    f, _, _, _ = _facade("local")
    f.feed_block(b"z")   # resume 전 → active 없음, 예외 없음


def test_start_preloads_local_when_default_local():
    async def run():
        f, local, gladias, _ = _facade("local")
        await f.start()
        assert local.started == 1 and gladias == []
    asyncio.run(run())


def test_aclose_closes_active_and_local():
    async def run():
        f, local, gladias, state = _facade("local")
        await f.resume()                 # local
        state["backend"] = "gladia"; await f.resume()   # gladia active, local still alive
        await f.aclose()
        assert gladias[0].closed == 1 and local.closed == 1
    asyncio.run(run())
