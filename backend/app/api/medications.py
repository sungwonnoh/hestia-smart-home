from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ..dependencies import Container, get_container
from ..schemas.medication import MedicationIn, MedicationOut

router = APIRouter(tags=["medications"])


@router.get("/medications", response_model=List[MedicationOut])
def list_medications(c: Container = Depends(get_container)) -> List[MedicationOut]:
    return c.medications.list()


@router.post("/medications", response_model=MedicationOut, status_code=status.HTTP_201_CREATED)
def create_medication(body: MedicationIn, c: Container = Depends(get_container)) -> MedicationOut:
    return c.medications.add(body)


@router.put("/medications/{medication_id}", response_model=MedicationOut)
def update_medication(
    medication_id: str, body: MedicationIn, c: Container = Depends(get_container)
) -> MedicationOut:
    updated = c.medications.update(medication_id, body)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "복약 일정을 찾을 수 없습니다.")
    return updated


@router.delete("/medications/{medication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_medication(medication_id: str, c: Container = Depends(get_container)) -> Response:
    if not c.medications.delete(medication_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "복약 일정을 찾을 수 없습니다.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
