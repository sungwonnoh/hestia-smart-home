from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import Container, get_container
from ..repositories.device_repository import ConflictError, NotFoundError
from ..schemas.device import DeviceOut, DeviceUpdate

router = APIRouter(tags=["devices"])


@router.get("/devices", response_model=List[DeviceOut])
def list_devices(c: Container = Depends(get_container)) -> List[DeviceOut]:
    """MQTT 원본 대신 Flutter Device 모델로 변환해 돌려준다."""
    return c.devices.list()


@router.put("/devices/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: str,
    body: DeviceUpdate,
    c: Container = Depends(get_container),
) -> DeviceOut:
    try:
        record = c.device_repo.update_device(device_id, body.model_dump(exclude_unset=True))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"가전을 찾을 수 없습니다: {device_id}") from exc
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if record.virtual_id:
        last = c.cache.device(record.virtual_id)
        if last is not None:
            c.device_repo.save_state(record.virtual_id, *last)
            record = c.device_repo.get_device(device_id)
    return c.devices.to_out(record)
