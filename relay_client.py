"""회의 모드용 outbound WebSocket publisher.

용도: MeetingSession._emit(kind, text) 가 호출될 때마다 외부 relay 서버
(meeting-web Cloudflare Worker) 로 이벤트를 송신한다. 자비스 콘솔 출력은
그대로 두고, listener 패턴으로 추가만 한다.

설계 원칙:
- 회의 자체를 막지 않는다: emit() 은 큐 적재만 (즉시 return). 송신 실패는
  콘솔 로그로만 표시되고 회의 흐름은 계속 진행.
- 끊김 시 자동 재연결 (지수 백오프). 재연결되면 큐에 쌓인 메시지부터 송신.
- close() 는 end 이벤트를 보내고 깔끔히 정리.
"""
import asyncio
import json
import struct
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import quote

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except Exception:  # pragma: no cover — websockets 미설치 환경
    websockets = None  # type: ignore
    ConnectionClosed = Exception  # type: ignore


class RelayClient:
    """outbound WebSocket publisher. 회의 1개당 1개 인스턴스."""

    def __init__(self, url: str, token: str, meta: Any, *,
                 on_log=print, max_queue: int = 1000,
                 connect_timeout: float = 5.0):
        # url 정규화: 끝의 / 제거. /publish/<key> 는 send_loop 에서 붙임.
        self.base_url = url.rstrip("/")
        self.token = token
        self.meta = meta            # MeetingMeta (live_translate.MeetingMeta)
        self.on_log = on_log
        self.connect_timeout = connect_timeout

        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ws = None
        self._connected = asyncio.Event()   # 한 번이라도 연결되면 set

    # --- public API ---

    async def connect(self) -> bool:
        """sender 태스크를 띄우고 첫 연결을 시도. 첫 hello 송신까지 성공하면 True."""
        if websockets is None:
            self.on_log("[relay] websockets 미설치 — pip install websockets")
            return False
        # hello 를 큐 첫 번째로 미리 넣어둠 (재연결 때마다 다시 보내야 하니
        # 아래 _connect_once 에서 별도로 처리. 큐엔 넣지 않는다.)
        self._task = asyncio.create_task(self._run(), name="relay-sender")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.connect_timeout)
            return True
        except asyncio.TimeoutError:
            self.on_log(f"[relay] 연결 시간 초과: {self.base_url}")
            return False

    def emit(self, kind: str, text: str = "") -> None:
        """이벤트 enqueue (동기). 큐가 가득 차면 드롭(콘솔에 경고)."""
        msg = {"kind": kind, "text": text}
        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.on_log("[relay] 큐 가득참 — 메시지 드롭")

    async def emit_async(self, kind: str, text: str = "") -> None:
        """MeetingSession.add_listener 가 async 콜백을 기대하므로 await 가능 래퍼."""
        self.emit(kind, text)

    def emit_audio(self, pcm_bytes: bytes, sr: int) -> None:
        """TTS PCM(int16 LE)을 binary 프레임으로 enqueue: [4B sr LE][int16 PCM]."""
        frame = struct.pack("<I", int(sr)) + bytes(pcm_bytes)
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.on_log("[relay] 큐 가득참 — 오디오 드롭")

    async def close(self) -> None:
        """end 송신 후 sender 정리. 회의 종료 시 호출."""
        if self._task is None:
            return
        # end 를 큐에 넣어 sender 가 정상 송신 후 종료
        try:
            self._queue.put_nowait({"kind": "end"})
        except asyncio.QueueFull:
            pass
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=3.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    # --- internal ---

    def _hello_payload(self) -> dict:
        """meta 를 hello 메시지로 직렬화."""
        meta_d = asdict(self.meta) if hasattr(self.meta, "__dataclass_fields__") else dict(self.meta)
        # MeetingMeta.key 는 property 라 asdict 결과에 자동 포함되지 않음 — 수동으로
        if hasattr(self.meta, "key"):
            meta_d["key"] = self.meta.key
        meta_d.setdefault("started_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        # 표준화: partner_name/my_name → partner/user 매핑
        if "partner_name" in meta_d:
            meta_d["partner"] = meta_d.pop("partner_name")
        if "my_name" in meta_d:
            meta_d["user"] = meta_d.pop("my_name")
        if "my_lang" in meta_d:
            meta_d["user_lang"] = meta_d.pop("my_lang")
        return {"kind": "hello", "meta": meta_d}

    def _publish_url(self) -> str:
        key = quote(getattr(self.meta, "key", "default"), safe="")
        return f"{self.base_url}/publish/{key}"

    async def _run(self) -> None:
        """sender 메인 루프. 끊기면 백오프 후 재연결. _stop 신호 받으면 종료."""
        backoff = 0.5
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 0.5   # 성공했으니 리셋
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_log(f"[relay] 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return   # stop signal
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _send_item(self, ws, item) -> None:
        if isinstance(item, (bytes, bytearray)):
            await ws.send(item)
        else:
            await ws.send(json.dumps(item, ensure_ascii=False))

    async def _connect_once(self) -> None:
        url = self._publish_url()
        headers = {"Authorization": f"Bearer {self.token}"}
        async with websockets.connect(
            url, additional_headers=headers, ping_interval=20, ping_timeout=10,
            open_timeout=self.connect_timeout,
        ) as ws:
            self._ws = ws
            # 1) 매 연결마다 hello 재송신 (재연결 시 새 publisher 로 인수됨)
            await ws.send(json.dumps(self._hello_payload(), ensure_ascii=False))
            self._connected.set()
            # 2) 큐 소비
            while not self._stop.is_set():
                try:
                    msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    await self._send_item(ws, msg)
                except ConnectionClosed:
                    # 큐에 다시 넣고 바깥 _run 에서 재연결
                    try:
                        self._queue.put_nowait(msg)
                    except asyncio.QueueFull:
                        pass
                    raise
                if not isinstance(msg, (bytes, bytearray)) and msg.get("kind") == "end":
                    # end 송신 후 깔끔 종료
                    return
            # stop signal: 큐에 남은 거 더 보낼 필요 없음
            return
