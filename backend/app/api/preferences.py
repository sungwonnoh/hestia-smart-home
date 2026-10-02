from fastapi import APIRouter, Depends

from ..dependencies import Container, get_container
from ..schemas.preference import UserPreferences

router = APIRouter(tags=["preferences"])


@router.get("/preferences", response_model=UserPreferences)
def get_preferences(c: Container = Depends(get_container)) -> UserPreferences:
    return c.preference_repo.get()


@router.put("/preferences", response_model=UserPreferences)
def save_preferences(
    body: UserPreferences,
    c: Container = Depends(get_container),
) -> UserPreferences:
    return c.preference_repo.save(body)
