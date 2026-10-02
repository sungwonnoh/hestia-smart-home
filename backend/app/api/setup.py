from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import Container, get_container
from ..schemas.setup import HomeSetupIn, HomeSetupOut

router = APIRouter(tags=["setup"])


def _current(c: Container) -> HomeSetupOut:
    return HomeSetupOut(
        rooms=c.device_repo.list_rooms(),
        devices=c.devices.list(),
        preferences=c.preference_repo.get(),
    )


@router.get("/setup", response_model=HomeSetupOut)
def get_setup(c: Container = Depends(get_container)) -> HomeSetupOut:
    """최초 설정 전이면 404. Flutter 는 이것으로 Onboarding 여부를 정한다."""
    if not c.device_repo.is_configured():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "최초 설정이 아직 없습니다.")
    return _current(c)


@router.put("/setup", response_model=HomeSetupOut)
def save_setup(body: HomeSetupIn, c: Container = Depends(get_container)) -> HomeSetupOut:
    """공간·가전·알림 설정을 통째로 저장한다. 가전은 같은 종류의 MQTT 장치에 연결한다."""
    c.device_repo.replace_setup(body.rooms, body.devices)
    c.preference_repo.save(body.preferences)
    c.devices.bind_unassigned()
    return _current(c)
