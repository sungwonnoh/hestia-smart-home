"""HESTIA FastAPI Backend Adapter.

Flutter ← REST → FastAPI ← MQTT → Context Engine
Monitor ← WebSocket ┘

실행: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI

from .api import (context, devices, explanations, health, medications, notifications,
                  preferences, rooms, setup)
from .config import API_VERSION, Settings
from .dependencies import build_container
from .realtime import monitor_ws

API_PREFIX = "/api/v1"


def create_app(settings: Optional[Settings] = None, mqtt: Any = None) -> FastAPI:
    """settings/mqtt 는 테스트에서 바꿔 끼운다."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = build_container(settings, mqtt)
        container.monitor.attach_loop(asyncio.get_running_loop())
        app.state.hestia = container
        container.mqtt.start()
        try:
            yield
        finally:
            container.mqtt.stop()
            container.db.close()

    app = FastAPI(
        title="HESTIA API",
        version=API_VERSION,
        description="Flutter 와 Context Engine/MQTT 사이의 Backend Adapter",
        lifespan=lifespan,
    )
    for module in (health, rooms, devices, context, notifications,
                   explanations, preferences, setup, medications):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(monitor_ws.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
