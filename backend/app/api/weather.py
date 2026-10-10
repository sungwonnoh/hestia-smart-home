from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import Container, get_container
from ..schemas.weather import Weather

router = APIRouter(tags=["weather"])


@router.get("/weather", response_model=Weather)
def current_weather(c: Container = Depends(get_container)) -> Weather:
    """RPi4 가 보낸 최신 외부 날씨. 아직 받지 않았으면 404."""
    weather = c.weather.latest()
    if weather is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "아직 날씨를 받지 못했습니다.")
    return weather
