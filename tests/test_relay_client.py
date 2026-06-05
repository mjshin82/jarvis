# tests/test_relay_client.py
import asyncio
import struct

from relay_client import RelayClient


def _rc():
    return RelayClient("ws://x", "tok", {"key": "room"}, on_log=lambda *_: None)


def test_emit_audio_enqueues_binary_with_sr_header():
    rc = _rc()
    rc.emit_audio(b"\x01\x02\x03\x04", 22050)
    item = rc._queue.get_nowait()
    assert isinstance(item, (bytes, bytearray))
    assert struct.unpack("<I", item[:4])[0] == 22050
    assert bytes(item[4:]) == b"\x01\x02\x03\x04"


def test_emit_enqueues_json_dict():
    rc = _rc()
    rc.emit("assistant", "안녕")
    item = rc._queue.get_nowait()
    assert item == {"kind": "assistant", "text": "안녕"}


def test_send_item_routes_bytes_vs_json():
    rc = _rc()
    sent = []

    class FakeWS:
        async def send(self, data): sent.append(data)

    async def main():
        ws = FakeWS()
        await rc._send_item(ws, {"kind": "user", "text": "hi"})
        await rc._send_item(ws, struct.pack("<I", 16000) + b"\xaa\xbb")

    asyncio.run(main())
    assert sent[0] == '{"kind": "user", "text": "hi"}'
    assert isinstance(sent[1], (bytes, bytearray)) and sent[1][:4] == struct.pack("<I", 16000)
