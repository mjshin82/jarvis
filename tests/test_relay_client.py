# tests/test_relay_client.py
import asyncio
import json
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


class _Meta:
    key = "k"


def _rc_meta():
    return RelayClient("ws://x", "tok", _Meta(), on_log=lambda *a: None)


def test_handle_inbound_updates_count():
    rc = _rc_meta()
    assert rc.web_viewer_count == 0
    rc._handle_inbound('{"kind":"viewers","count":3}')
    assert rc.web_viewer_count == 3
    rc._handle_inbound('{"kind":"viewers","count":0}')
    assert rc.web_viewer_count == 0


def test_handle_inbound_ignores_others():
    rc = _rc_meta()
    rc._handle_inbound('{"kind":"viewers","count":2}')
    rc._handle_inbound('{"kind":"something"}')   # 무시 — 유지
    rc._handle_inbound("not json")               # 안전
    rc._handle_inbound(b"\x00\x01")              # bytes 무시
    assert rc.web_viewer_count == 2


def test_emit_includes_lang_when_set():
    rc = _rc()
    rc.emit("translation", "hello", lang="ja")
    assert rc._queue.get_nowait() == {"kind": "translation", "text": "hello", "lang": "ja"}


def test_emit_omits_lang_when_empty():
    rc = _rc()
    rc.emit("source", "안녕")
    assert rc._queue.get_nowait() == {"kind": "source", "text": "안녕"}


def test_handle_inbound_archive_request_calls_callback():
    rc = _rc()
    got = []
    rc.on_archive_request = lambda m: got.append(m)
    rc._handle_inbound(json.dumps({"kind": "archive_request", "text": "{}"}))
    assert len(got) == 1 and got[0]["kind"] == "archive_request"


def test_handle_inbound_archive_request_no_callback_safe():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "archive_request", "text": "{}"}))  # on_archive_request=None → no crash


def test_handle_inbound_viewers_still_works():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "viewers", "count": 3}))
    assert rc.web_viewer_count == 3


def test_handle_inbound_list_request_calls_callback():
    rc = _rc()
    got = []
    rc.on_list_request = lambda m: got.append(m)
    rc._handle_inbound(json.dumps({"kind": "list_request", "text": "{}"}))
    assert len(got) == 1 and got[0]["kind"] == "list_request"


def test_handle_inbound_list_request_no_callback_safe():
    rc = _rc()
    rc._handle_inbound(json.dumps({"kind": "list_request", "text": "{}"}))  # on_list_request=None → no crash
