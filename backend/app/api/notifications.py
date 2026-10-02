from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..dependencies import Container, get_container
from ..schemas.notification import AckRequest, AckResponse, NotificationOut
from ..services.notification_service import NotificationNotFound

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=List[NotificationOut])
def list_notifications(
    limit: int = Query(100, ge=1, le=500),
    c: Container = Depends(get_container),
) -> List[NotificationOut]:
    """최신순. 취소된 알림은 빠진다. 조회만으로 DELIVERED/SEEN 처리하지 않는다."""
    return c.notifications.list(limit)


@router.post("/notifications/{notification_id}/ack", response_model=AckResponse)
def acknowledge(
    notification_id: str,
    body: AckRequest,
    c: Container = Depends(get_container),
) -> AckResponse:
    try:
        forwarded = c.notifications.acknowledge(notification_id, body.ack_type)
    except NotificationNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"알림을 찾을 수 없습니다: {notification_id}"
        ) from exc
    return AckResponse(success=True, forwarded=forwarded)
