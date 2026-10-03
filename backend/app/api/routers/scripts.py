"""Student script upload, extraction, and script detail endpoints."""

from pathlib import Path
from uuid import uuid4

from backend.app.core.settings import PAGE_IMAGE_DIR, UPLOAD_DIR
from backend.app.db.database import (
    get_db,
    get_evaluations_for_script,
    get_extractions_for_script,
    get_script,
    get_script_totals,
    get_student,
    init_db,
    insert_script,
    list_all_scripts,
    upsert_student,
)
from backend.app.db.exams import get_exam
from backend.app.services.models.ollama import ModelUnavailable
from backend.app.services.ocr.process import process_script_file
from backend.app.services.storage import delete_upload, save_upload
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/scripts", tags=["scripts"])
init_db()


@router.post("/upload")
async def upload_script(
    file: UploadFile,
    student_id: str | None = Form(None),
    student_name: str | None = Form(None),
    exam_id: str | None = Form(None),
) -> dict[str, object]:
    if exam_id:
        with get_db() as conn:
            if not get_exam(conn, exam_id):
                raise HTTPException(status_code=404, detail="Exam not found")

    script_id, path, size = await save_upload(file)
    sid = (student_id or "").strip() or uuid4().hex
    name = (student_name or "").strip() or Path(file.filename or "").stem or sid
    upsert_student(sid, name)
    insert_script(script_id, sid, file.filename or "script", str(path), exam_id or None)
    return {
        "script_id": script_id,
        "student_id": sid,
        "exam_id": exam_id,
        "filename": file.filename,
        "content_type": file.content_type,
        "bytes": size,
        "path": str(path),
        "message": "Upload saved.",
    }


@router.post("/{script_id}/process")
def process_script(script_id: str, body: dict | None = None) -> dict[str, object]:
    matches = list(UPLOAD_DIR.glob(f"{script_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Script not found")
    script_path = matches[0]
    with get_db() as conn:
        script_row = get_script(conn, script_id)
        exam = (
            get_exam(conn, script_row["exam_id"])
            if script_row and script_row.get("exam_id")
            else None
        )
    question_ids = [q["question_id"] for q in exam["questions"]] if exam else None
    try:
        extracted = process_script_file(script_id, script_path, question_ids or None)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"VLM extraction is required and unavailable: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    questions = extracted["questions"]
    return {
        "script_id": script_id,
        "source": str(script_path),
        "page_count": extracted["page_count"],
        "pages": extracted["pages"],
        "extraction": {
            "method": extracted["method"],
            "page_count": extracted["page_count"],
            "ocr_required": False,
            "text": "\n".join(f"{qid}: {text}" for qid, text in questions.items()),
            "questions": questions,
            "vlm_model": extracted["vlm_model"],
            "conflicts": extracted.get("conflicts", []),
        },
        "extractions": extracted["extractions"],
    }


@router.get("")
def list_scripts() -> dict[str, object]:
    with get_db() as conn:
        return {"scripts": list_all_scripts(conn)}


@router.get("/{script_id}/extractions")
@router.get("/{script_id}/crops", include_in_schema=False)
def get_script_extractions(script_id: str) -> dict[str, object]:
    with get_db() as conn:
        return {"script_id": script_id, "extractions": get_extractions_for_script(conn, script_id)}


@router.get("/{script_id}/pages/{page_number}/image")
def get_page_image(script_id: str, page_number: int) -> FileResponse:
    page_dir = PAGE_IMAGE_DIR / script_id
    if not page_dir.is_dir():
        raise HTTPException(status_code=404, detail="No rendered pages for this script")
    matches = sorted(page_dir.glob(f"*_page_{page_number}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Page {page_number} image not found")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(matches[0].suffix.lower(), "application/octet-stream")
    return FileResponse(str(matches[0]), media_type=media_type)


@router.get("/{script_id}")
def get_script_detail(script_id: str) -> dict[str, object]:
    with get_db() as conn:
        script = get_script(conn, script_id)
        if not script:
            raise HTTPException(status_code=404, detail="Script not found")
        extractions = get_extractions_for_script(conn, script_id)
        evals = get_evaluations_for_script(conn, script_id)
        totals = get_script_totals(conn, script_id)
        exam = get_exam(conn, script["exam_id"]) if script.get("exam_id") else None
        student = get_student(conn, script["student_id"])
    answer_by_question = {item["question_id"]: item["extracted_text"] for item in extractions}
    for evaluation in evals:
        evaluation["answer_text"] = evaluation.get("answer_text") or answer_by_question.get(
            evaluation["question_id"], ""
        )
    return {
        "script": script,
        "student": student,
        "exam": {"exam_id": exam["exam_id"], "name": exam["name"]} if exam else None,
        "extractions": extractions,
        "evaluations": evals,
        "totals": totals,
    }


@router.delete("/{script_id}")
def delete_script(script_id: str) -> dict[str, str]:
    with get_db() as conn:
        if not get_script(conn, script_id):
            raise HTTPException(status_code=404, detail="Script not found")
        conn.execute("DELETE FROM scripts WHERE script_id=?", (script_id,))
    for leftover in UPLOAD_DIR.glob(f"{script_id}.*"):
        delete_upload(script_id, leftover)
    return {"deleted": script_id}
