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
