"""WS /ws/monitor — HESTIA Monitor 실시간 전달.

접속하면 현재 상태(snapshot)를 한 번 보내고, 이후 MQTT 로 들어오는
이벤트를 그대로 흘려보낸다. 시각화용 전달 계층이며 판단하지 않는다.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/monitor")
async def monitor(websocket: WebSocket) -> None:
    container = websocket.app.state.hestia
    hub = container.monitor
    await websocket.accept()
    queue = hub.subscribe()
    receiver = None
    try:
        await websocket.send_json({
            "type": "snapshot",
            "sent_ts": int(time.time()),
            **container.cache.snapshot(),
        })
        receiver = asyncio.ensure_future(_drain_client(websocket))
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {getter, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in done:  # 클라이언트가 연결을 닫음
                getter.cancel()
                break
            await websocket.send_json(getter.result())
    except WebSocketDisconnect:
        pass
    finally:
        if receiver is not None:
            receiver.cancel()
        hub.unsubscribe(queue)


async def _drain_client(websocket: WebSocket) -> None:
    """클라이언트가 보내는 것은 쓰지 않는다. 연결 종료 감지용."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
