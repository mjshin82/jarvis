# remote_mic_receiver.py
"""relay 의 /mic-recv/<key> 에 붙어 외부 마이크 binary 프레임을 받는 인바운드 클라이언트.

relay_client.py(아웃바운드 publisher)와 대칭. 회의 모드와 독립적으로, REMOTE_MIC_ENABLED
일 때 메인 흐름에서 항상 떠 있는다. 끊기면 지수 백오프로 재연결.
"""
import asyncio
import json
from urllib.parse import quote

from ws_backoff import reconnect_loop

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore


class RemoteMicReceiver:
    def __init__(self, url, token, router, *, on_log=print, key=None,
                 connect_timeout=5.0):
        self.base_url = url.rstrip("/")
        self.token = token
        self.router = router
        self.on_log = on_log
        self.key = key
        self.connect_timeout = connect_timeout
        self._stop = asyncio.Event()
        self._task = None
        self._outbound: asyncio.Queue = asyncio.Queue()
        self._last_source = None

    def _url(self):
        key = quote(self.key or "jarvis", safe="")
        return f"{self.base_url}/mic-recv/{key}"

    async def _handle_message(self, data):
        if isinstance(data, (bytes, bytearray)):
            self.router.on_remote_frame(bytes(data))
            return
        try:
            msg = json.loads(data)
        except Exception:
            return
        kind = msg.get("kind")
        if kind == "no_receiver":
            self.on_log("[mic] relay: 수신자 없음 통지")
        elif kind in ("mic_start", "mic_stop"):
            self.on_log(f"[mic] 원격 캡처 {kind}")

    def notify_source(self, source) -> None:
        """MicRouter.on_switch 로 연결 — 소스 상태를 relay 로 올린다(동기, 큐 적재).
        MicRouter 내부 어휘(local/remote)를 웹 계약(system/remote)으로 정규화한다."""
        web_source = "system" if source == "local" else source
        self._last_source = web_source
        try:
            self._outbound.put_nowait({"kind": "mic_source", "source": web_source})
        except asyncio.QueueFull:
            pass

    def start(self):
        if websockets is None:
            self.on_log("[mic] websockets 미설치 — 원격 마이크 비활성")
            return None
        self._task = asyncio.create_task(self._run(), name="remote-mic-rx")
        return self._task

    async def close(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _send_loop(self, ws) -> None:
        while True:
            msg = await self._outbound.get()
            await ws.send(json.dumps(msg))

    async def _recv_loop(self, ws) -> None:
        async for message in ws:
            await self._handle_message(message)

    async def _run(self):
        await reconnect_loop(self._connect_once, self._stop, self.on_log, label="mic")

    async def _connect_once(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self.on_log("[mic] 원격 마이크 수신 대기 중")
            # (re)연결 직후 현재 소스 1회 동기화 (끊김 사이 전환 복구)
            if self._last_source is not None:
                await ws.send(json.dumps({"kind": "mic_source", "source": self._last_source}))
            recv = asyncio.create_task(self._recv_loop(ws))
            send = asyncio.create_task(self._send_loop(ws))
            try:
                done, pending = await asyncio.wait({recv, send}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # 정상 종료든 외부 취소든 두 태스크를 반드시 정리
                for t in (recv, send):
                    if not t.done():
                        t.cancel()
                for t in (recv, send):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
            # 비취소 예외만 위로 전파(바깥 _run 백오프 재연결). 취소된 태스크는 건너뜀.
            for t in done:
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    raise exc
