"""Student records and student script history."""

from typing import Any
from uuid import uuid4

from backend.app.api.deps import ContainerDep
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/students", tags=["students"])


@router.get("")
def get_students(c: ContainerDep) -> dict[str, object]:
    return {"students": c.students.list_all()}


@router.post("")
def create_student(body: dict[str, Any], c: ContainerDep) -> dict[str, object]:
    student_id = body.get("student_id") or uuid4().hex
    name = body.get("name", "")
    c.students.upsert(student_id, name)
    return {"student_id": student_id, "name": name}


@router.get("/{student_id}")
def get_student_detail(student_id: str, c: ContainerDep) -> dict[str, object]:
    student = c.students.get(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"student": student, "scripts": c.scripts.list_for_student(student_id)}
