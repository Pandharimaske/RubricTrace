"""Student records and student script history."""

from uuid import uuid4

from backend.app.db.database import get_db, get_student, list_students, upsert_student
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/students", tags=["students"])


@router.get("")
def get_students() -> dict[str, object]:
    with get_db() as conn:
        return {"students": list_students(conn)}


@router.post("")
def create_student(body: dict) -> dict[str, object]:
    student_id = body.get("student_id") or uuid4().hex
    name = body.get("name", "")
    upsert_student(student_id, name)
    return {"student_id": student_id, "name": name}


@router.get("/{student_id}")
def get_student_detail(student_id: str) -> dict[str, object]:
    with get_db() as conn:
        student = get_student(conn, student_id)
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")
        from backend.app.db.database import list_scripts_for_student

        scripts = list_scripts_for_student(conn, student_id)
    return {"student": student, "scripts": scripts}
