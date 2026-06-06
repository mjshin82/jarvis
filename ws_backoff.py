# ws_backoff.py
"""WS 클라이언트 공용 재연결 루프.

연결이 끊기면 지수 백오프(init→max)로 재시도하고, stop_event 가 set 되면 종료한다.
relay_client / remote_mic_receiver / control_receiver / gladia_stt 가 공유한다.
"""
import asyncio


async def reconnect_loop(connect_once, stop_event, on_log, *, label,
                         init_backoff=0.5, max_backoff=8.0):
    """connect_once 를 반복 호출. 예외 시 백오프 후 재시도, stop_event 시 정상 종료.

    connect_once: async 콜러블 — 한 번 연결해 끊길 때까지 유지.
    stop_event:   asyncio.Event — set 되면 루프 종료.
    on_log:       (str)->None — 실패 로그 출력.
    label:        로그 출처 식별자(예: "relay").
    """
    backoff = init_backoff
    while not stop_event.is_set():
        try:
            await connect_once()
            backoff = init_backoff          # 성공 → 리셋
        except asyncio.CancelledError:
            return
        except Exception as e:
            on_log(f"[{label}] 연결 끊김/실패: {e} — {backoff:.1f}s 후 재시도")
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            return                          # stop 신호
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, max_backoff)
