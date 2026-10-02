from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import Container, get_container
from ..repositories.device_repository import ConflictError
from ..schemas.setup import Room

router = APIRouter(tags=["rooms"])


@router.get("/rooms", response_model=List[Room])
def list_rooms(c: Container = Depends(get_container)) -> List[Room]:
    return c.device_repo.list_rooms()


@router.post("/rooms", response_model=Room, status_code=status.HTTP_201_CREATED)
def create_room(room: Room, c: Container = Depends(get_container)) -> Room:
    try:
        return c.device_repo.create_room(room)
    except ConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
