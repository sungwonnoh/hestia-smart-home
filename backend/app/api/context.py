from typing import Any, Dict

from fastapi import APIRouter, Depends

from ..dependencies import Container, get_container

router = APIRouter(tags=["context"])


@router.get("/context/current")
def current_context(c: Container = Depends(get_container)) -> Dict[str, Dict[str, Any]]:
    """Context Engine 이 보낸 context 6종의 최신 값. 아직 받지 않은 것은 빠진다."""
    return c.cache.current_context()
