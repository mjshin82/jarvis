# remote_mic_receiver.py
"""relay 의 /mic-recv/<key> 에 붙어 외부 마이크 binary 프레임을 받는 인바운드 클라이언트.

relay_client.py(아웃바운드 publisher)와 대칭. 회의 모드와 독립적으로, REMOTE_MIC_ENABLED
일 때 메인 흐름에서 항상 떠 있는다. 끊기면 지수 백오프로 재연결.
"""
import asyncio
import json
from urllib.parse import quote

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

    async def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 0.5
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_log(f"[mic] 수신 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _connect_once(self):
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            self._url(), additional_headers=headers,
            ping_interval=20, ping_timeout=10, open_timeout=self.connect_timeout,
        ) as ws:
            self.on_log("[mic] 원격 마이크 수신 대기 중")
            async for message in ws:
                await self._handle_message(message)
