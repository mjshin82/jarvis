# tests/test_remote_mic_receiver.py
import asyncio

from remote_mic_receiver import RemoteMicReceiver


class FakeRouter:
    def __init__(self):
        self.frames = []

    def on_remote_frame(self, pcm):
        self.frames.append(pcm)


def test_binary_message_goes_to_router_json_logged():
    logs = []
    router = FakeRouter()
    rx = RemoteMicReceiver("ws://x", "tok", router, on_log=logs.append)

    async def main():
        await rx._handle_message(b"\x00\x01\x02\x03")          # binary → router
        await rx._handle_message('{"kind":"no_receiver"}')     # json 제어 → 로그
        await rx._handle_message('not json')                   # 무시(예외 없음)

    asyncio.run(main())
    assert router.frames == [b"\x00\x01\x02\x03"]
    assert any("no_receiver" in m or "수신" in m for m in logs)


def test_recv_url_built_from_base_and_key():
    rx = RemoteMicReceiver("wss://relay.example/", "tok", FakeRouter(),
                           on_log=lambda *_: None, key="room1")
    assert rx._url() == "wss://relay.example/mic-recv/room1"


def test_notify_source_caches_and_enqueues():
    rx = RemoteMicReceiver("ws://x", "tok", FakeRouter(), on_log=lambda *_: None)
    rx.notify_source("remote")
    assert rx._last_source == "remote"
    msg = rx._outbound.get_nowait()
    assert msg == {"kind": "mic_source", "source": "remote"}


def test_send_loop_writes_queued_to_ws():
    rx = RemoteMicReceiver("ws://x", "tok", FakeRouter(), on_log=lambda *_: None)

    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send(self, data):
            self.sent.append(data)

    async def main():
        ws = FakeWS()
        rx.notify_source("system")
        task = asyncio.create_task(rx._send_loop(ws))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return ws.sent

    sent = asyncio.run(main())
    assert any('"kind": "mic_source"' in s and '"source": "system"' in s for s in sent)
