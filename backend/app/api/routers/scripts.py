"""Student script upload, extraction, and script detail endpoints."""

import hashlib
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.api.deps import ContainerDep
from backend.app.container import Container
from backend.app.core.storage import UploadRejected
from backend.app.db.repositories import ScriptConflict
from backend.app.llm.errors import ModelUnavailable
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/scripts", tags=["scripts"])

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


# One render at a time: the review table asks for many thumbnails of the same script at once,
# and two requests must not render (and write) the same PDF concurrently.
_RENDER_LOCK = threading.Lock()


def _page_files(page_dir: Path, page_number: int) -> list[Path]:
    return sorted(page_dir.glob(f"*_page_{page_number}.*")) if page_dir.is_dir() else []


def _render_pages(
    c: Container, script: dict[str, Any], page_dir: Path, page_number: int
) -> list[Path]:
    source = Path(script["file_path"])
    if not source.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"The original file is missing ({source}), so page images can't be rendered.",
        )
    try:
        pages = c.reader.render_pages(source, page_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if script.get("page_count") != len(pages):
        c.scripts.set_page_count(script["script_id"], len(pages))
    matches = _page_files(page_dir, page_number)
    if not matches:
        raise HTTPException(
            status_code=404, detail=f"Page {page_number} does not exist ({len(pages)} pages)."
        )
    return matches


@router.post("/upload")
async def upload_script(
    file: UploadFile,
    c: ContainerDep,
    student_id: str | None = Form(None),
    student_name: str | None = Form(None),
    exam_id: str = Form(...),
) -> dict[str, object]:
    if not c.exams.get(exam_id):
        raise HTTPException(status_code=404, detail="Exam not found")

    # Reject oversize uploads before reading them into memory when the size is known.
    if file.size is not None and file.size > c.config.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than the {c.config.max_upload_mb} MB limit.",
        )
    content = await file.read()
    try:
        stored = c.storage.save(file.filename or "", file.content_type, content)
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sid = (student_id or "").strip() or uuid4().hex
    name = (student_name or "").strip() or Path(file.filename or "").stem or sid
    try:
        c.scripts.insert(
            stored.script_id,
            sid,
            file.filename or "script",
            str(stored.path),
            exam_id,
            file_sha256=hashlib.sha256(content).hexdigest(),
            student_name=name,
        )
    except (ScriptConflict, LookupError) as exc:
        c.storage.delete(stored.script_id)  # nothing refers to the saved copy any more
        status = 409 if isinstance(exc, ScriptConflict) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "script_id": stored.script_id,
        "student_id": sid,
        "exam_id": exam_id,
        "filename": file.filename,
        "content_type": file.content_type,
        "bytes": stored.size,
        "path": str(stored.path),
        "message": "Upload saved.",
    }


@router.post("/{script_id}/process")
def process_script(script_id: str, c: ContainerDep) -> dict[str, object]:
    script = c.scripts.get(script_id)
    if not script or not Path(script["file_path"]).is_file():
        raise HTTPException(status_code=404, detail="Script not found")
    script_path = Path(script["file_path"])
    try:
        processed = c.processor.process(script["script_id"], script_path)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"VLM extraction is required and unavailable: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reading = processed.reading
    return {
        "script_id": script_id,
        "source": str(script_path),
        "page_count": reading.page_count,
        "pages": reading.pages,
        "extraction": {
            "method": reading.method,
            "page_count": reading.page_count,
            "ocr_required": False,
            "text": "\n".join(f"{qid}: {text}" for qid, text in reading.questions.items()),
            "questions": reading.questions,
            "vlm_model": reading.vlm_model,
            "conflicts": reading.conflicts,
        },
        "extractions": processed.extractions,
    }


@router.get("")
def list_scripts(c: ContainerDep) -> dict[str, object]:
    return {"scripts": c.scripts.list_all()}


@router.get("/{script_id}/extractions")
@router.get("/{script_id}/crops", include_in_schema=False)
def get_script_extractions(script_id: str, c: ContainerDep) -> dict[str, object]:
    return {"script_id": script_id, "extractions": c.extractions.for_script(script_id)}


@router.get("/{script_id}/pages/{page_number}/image")
def get_page_image(script_id: str, page_number: int, c: ContainerDep) -> FileResponse:
    script = c.scripts.get(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    page_dir = c.config.page_image_dir / script["script_id"]
    # Scripts inserted straight into the database (e.g. the imported dataset) were never
    # rendered, so render them from the original file the first time a page is requested.
    # The lock is taken even to look: a page file seen mid-render may be half-written.
    with _RENDER_LOCK:
        matches = _page_files(page_dir, page_number) or _render_pages(
            c, script, page_dir, page_number
        )
    media_type = _IMAGE_TYPES.get(matches[0].suffix.lower(), "application/octet-stream")
    return FileResponse(str(matches[0]), media_type=media_type)


@router.get("/{script_id}")
def get_script_detail(script_id: str, c: ContainerDep) -> dict[str, object]:
    script = c.scripts.get(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    extractions = c.extractions.for_script(script_id)
    evaluations = c.evaluations.for_script(script_id)
    totals = c.evaluations.totals_for_script(script_id)
    exam = c.exams.get(script["exam_id"]) if script.get("exam_id") else None
    student = c.students.get(script["student_id"])

    answer_by_question = {item["question_id"]: item["extracted_text"] for item in extractions}
    for evaluation in evaluations:
        evaluation["answer_text"] = evaluation.get("answer_text") or answer_by_question.get(
            evaluation["question_id"], ""
        )
    exam_summary: dict[str, Any] | None = (
        {"exam_id": exam["exam_id"], "name": exam["name"]} if exam else None
    )
    return {
        "script": script,
        "student": student,
        "exam": exam_summary,
        "extractions": extractions,
        "evaluations": evaluations,
        "totals": totals,
    }


@router.delete("/{script_id}")
def delete_script(script_id: str, c: ContainerDep) -> dict[str, str]:
    if not c.scripts.get(script_id):
        raise HTTPException(status_code=404, detail="Script not found")
    c.scripts.delete(script_id)
    c.storage.delete(script_id)
    return {"deleted": script_id}
