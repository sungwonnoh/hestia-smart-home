from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..dependencies import Container, get_container
from ..schemas.model import Explanation

router = APIRouter(tags=["explanations"])


@router.get("/explanations/latest", response_model=Explanation)
def latest_explanation(c: Container = Depends(get_container)) -> Explanation:
    explanation = c.explanations.latest()
    if explanation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "아직 설명할 판단이 없습니다.")
    return explanation


@router.get("/explanations", response_model=List[Explanation])
def list_explanations(
    limit: int = Query(20, ge=1, le=200),
    c: Container = Depends(get_container),
) -> List[Explanation]:
    return c.explanations.list(limit)


@router.get("/explanations/{explanation_id}", response_model=Explanation)
def get_explanation(explanation_id: str, c: Container = Depends(get_container)) -> Explanation:
    """알림의 explanationId 로 해당 판단을 연다 (Flutter getExplanation)."""
    explanation = c.explanations.get(explanation_id)
    if explanation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"판단을 찾을 수 없습니다: {explanation_id}")
    return explanation
