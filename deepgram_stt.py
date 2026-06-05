# deepgram_stt.py
"""Deepgram 스트리밍 STT (미팅용) — 공식 deepgram-sdk(v7) AsyncDeepgramClient 사용.
nova-3 멀티링구얼. interim → on_partial, 발화 종료(speech_final) → on_final. RealtimeSTT 대체."""
import asyncio

try:
    from deepgram import AsyncDeepgramClient
except Exception:  # pragma: no cover
    AsyncDeepgramClient = None  # type: ignore


class DeepgramSTT:
    def __init__(self, api_key, *, model="nova-2", language="", on_partial, on_final, on_log=print,
                 connect_timeout=5.0):
        self.api_key = api_key
        self.model = model or "nova-2"
        self.language = language or "multi"   # 한↔영 코드스위칭
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_log = on_log
        self.connect_timeout = connect_timeout
        self._client = None
        self._out_q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._task = None
        self._final_parts = []

    def feed_pcm(self, pcm16: bytes) -> None:
        try:
            self._out_q.put_nowait(pcm16)
        except asyncio.QueueFull:
            pass

    def _handle_dg_message(self, msg) -> None:
        """Deepgram Results 메시지(dict) → on_partial / on_final. (테스트 분리)"""
        if not isinstance(msg, dict) or msg.get("type") != "Results":
            return
        try:
            alt = msg["channel"]["alternatives"][0]
        except (KeyError, IndexError, TypeError):
            return
        text = (alt.get("transcript") or "").strip()
        if msg.get("is_final"):
            if text:
                self._final_parts.append(text)
            if msg.get("speech_final"):
                full = " ".join(self._final_parts).strip()
                self._final_parts = []
                if full:
                    self.on_final(full)
            elif text:
                self.on_partial(" ".join(self._final_parts).strip())
        elif text:
            prefix = " ".join(self._final_parts).strip()
            self.on_partial((prefix + " " + text).strip())

    async def start(self):
        if AsyncDeepgramClient is None:
            raise RuntimeError("deepgram-sdk 미설치 — Deepgram 불가")
        self._client = AsyncDeepgramClient(api_key=self.api_key)
        self._task = asyncio.create_task(self._run(), name="deepgram-stt")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=self.connect_timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError("Deepgram 연결 시간 초과")

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
                self.on_log(f"[deepgram] 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 8.0)

    async def _connect_once(self):
        async with self._client.listen.v1.connect(
            model=self.model,
            language=self.language,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            interim_results=True,
            punctuate=True,
            endpointing=300,
        ) as conn:
            self._connected.set()
            self.on_log(f"[deepgram] 연결됨 ({self.model}, {self.language})")
            send = asyncio.create_task(self._send_loop(conn))
            recv = asyncio.create_task(self._recv_loop(conn))
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

    async def _send_loop(self, conn):
        while not self._stop.is_set():
            try:
                pcm = await asyncio.wait_for(self._out_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await conn.send_media(pcm)

    async def _recv_loop(self, conn):
        async for message in conn:
            if getattr(message, "type", None) != "Results":
                continue
            try:
                alt = message.channel.alternatives[0]
            except (AttributeError, IndexError, TypeError):
                continue
            self._handle_dg_message({
                "type": "Results",
                "is_final": bool(getattr(message, "is_final", False)),
                "speech_final": bool(getattr(message, "speech_final", False)),
                "channel": {"alternatives": [{"transcript": (getattr(alt, "transcript", "") or "")}]},
            })
