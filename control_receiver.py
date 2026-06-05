# control_receiver.py
"""relay 의 /control-recv/<key> 에 붙어 브라우저발 제어 명령(JSON)을 받는 인바운드 클라이언트.

remote_mic_receiver.py 와 대칭이되 오디오/큐가 없는 JSON 전용. RELAY 설정 시 상시 연결,
끊기면 지수 백오프 재연결. `{kind:"meeting_stop"}` 수신 시 on_command("meeting_stop") 호출.
"""
import asyncio
import json
from urllib.parse import quote

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None  # type: ignore


class ControlReceiver:
    def __init__(self, url, token, *, on_command, on_log=print, key=None,
                 connect_timeout=5.0):
        self.base_url = url.rstrip("/")
        self.token = token
        self.on_command = on_command
        self.on_log = on_log
        self.key = key
        self.connect_timeout = connect_timeout
        self._stop = asyncio.Event()
        self._task = None

    def _url(self):
        key = quote(self.key or "jarvis", safe="")
        return f"{self.base_url}/control-recv/{key}"

    async def _handle_message(self, data):
        if isinstance(data, (bytes, bytearray)):
            return
        try:
            msg = json.loads(data)
        except Exception:
            return
        kind = msg.get("kind")
        if kind == "meeting_stop":
            await self.on_command("meeting_stop")
        elif kind == "no_receiver":
            self.on_log("[control] relay: 수신자 없음 통지")

    def start(self):
        if websockets is None:
            self.on_log("[control] websockets 미설치 — 웹 제어 비활성")
            return None
        self._task = asyncio.create_task(self._run(), name="control-rx")
        return self._task

    async def close(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _recv_loop(self, ws) -> None:
        async for message in ws:
            await self._handle_message(message)

    async def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 0.5
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_log(f"[control] 수신 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
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
            self.on_log("[control] 웹 제어 수신 대기 중")
            await self._recv_loop(ws)
