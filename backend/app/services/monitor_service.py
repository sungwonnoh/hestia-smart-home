"""HESTIA Monitor 로 이벤트를 흘려보내는 허브.

MQTT 수신은 paho 스레드에서 일어나므로, 이벤트를 asyncio 루프로 넘겨
연결마다 가진 큐에 넣는다. 판단은 하지 않고 전달만 한다.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 256


class MonitorHub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._queues)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """어느 스레드에서 불러도 된다."""
        loop = self._loop
        if loop is None or loop.is_closed() or not self._queues:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:  # 루프 종료 중
            pass

    def _fanout(self, event: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 느린 화면 하나 때문에 수신이 막히지 않게 가장 오래된 것을 버린다.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

