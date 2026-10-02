from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends

from ..config import API_VERSION
from ..dependencies import Container, get_container

router = APIRouter(tags=["health"])


@router.get("/health")
def health(c: Container = Depends(get_container)) -> Dict[str, Any]:
    """API 가 살아 있는지와 MQTT 연결 상태. DB 는 질의 한 번으로 확인한다."""
    c.db.query_one("SELECT 1")
    return {
        "status": "ok",
        "version": API_VERSION,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mqtt": {"enabled": c.mqtt.enabled, "connected": c.mqtt.connected},
        "profile": c.cache.profile,
        "monitorClients": c.monitor.client_count,
        "ingest": dict(c.ingest.stats),
    }
