# gladia_stt.py
"""Gladia 스트리밍 STT (미팅용). 2단계: REST init → WebSocket.
solaria-1 + 한↔영 code-switching. transcript(partial)→on_partial, is_final→on_final. RealtimeSTT 대체."""
import asyncio
import json

import requests
from ws_backoff import reconnect_loop

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore

_BASE = "https://api.gladia.io"


class GladiaSTT:
    def __init__(self, api_key, *, model="solaria-1", languages=("ko", "en"),
                 on_partial, on_final, on_log=print, connect_timeout=5.0):
        self.api_key = api_key
        self.model = model or "solaria-1"
        self.languages = list(languages) or ["ko", "en"]
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_log = on_log
        self.connect_timeout = connect_timeout
        self._out_q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._task = None

    def _config(self):
        return {
            "encoding": "wav/pcm",
            "bit_depth": 16,
            "sample_rate": 16000,
            "channels": 1,
            "model": self.model,
            "language_config": {"languages": self.languages, "code_switching": True},
            "messages_config": {
                "receive_partial_transcripts": True,
                "receive_final_transcripts": True,
            },
        }

    def _init_session(self):
        """동기 REST — 스레드에서 호출. 세션 ws url 반환."""
        r = requests.post(
            f"{_BASE}/v2/live?region=us-west",
            headers={"X-Gladia-Key": self.api_key},
            json=self._config(), timeout=self.connect_timeout,
        )
        if not r.ok:
            raise RuntimeError(f"Gladia init {r.status_code}: {(r.text or '')[:200]}")
        return r.json()

    def feed_pcm(self, pcm16: bytes) -> None:
        try:
            self._out_q.put_nowait(pcm16)
        except asyncio.QueueFull:
            pass

    def _handle_gladia_message(self, msg) -> None:
        """Gladia transcript 메시지 → on_partial / on_final. (테스트 분리)"""
        if not isinstance(msg, dict) or msg.get("type") != "transcript":
            return
        data = msg.get("data") or {}
        utt = data.get("utterance") or {}
        text = (utt.get("text") or "").strip()
        if not text:
            return
        if data.get("is_final"):
            self.on_final(text)
        else:
            self.on_partial(text)

    async def start(self):
        if websockets is None:
            raise RuntimeError("websockets 미설치 — Gladia 불가")
        self._task = asyncio.create_task(self._run(), name="gladia-stt")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.connect_timeout + 3)
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError("Gladia 연결 시간 초과")

    async def close(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _run(self):
        await reconnect_loop(self._connect_once, self._stop, self.on_log, label="gladia")

    async def _connect_once(self):
        resp = await asyncio.to_thread(self._init_session)
        url = resp["url"]
        async with websockets.connect(url, ping_interval=20, ping_timeout=10,
                                      open_timeout=self.connect_timeout) as ws:
            self._connected.set()
            self.on_log(f"[gladia] 연결됨 ({self.model}, {','.join(self.languages)})")
            send = asyncio.create_task(self._send_loop(ws))
            recv = asyncio.create_task(self._recv_loop(ws))
            try:
                done, pending = await asyncio.wait({send, recv}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in (send, recv):
                    if not t.done():
                        t.cancel()
                for t in (send, recv):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            for t in done:
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc

    async def _send_loop(self, ws):
        while not self._stop.is_set():
            try:
                pcm = await asyncio.wait_for(self._out_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await ws.send(pcm)

    async def _recv_loop(self, ws):
        async for raw in ws:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self._handle_gladia_message(msg)
