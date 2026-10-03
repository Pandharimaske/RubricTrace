"""Exam bounded-context endpoints: authoring, scripts, grading jobs, and results."""

from __future__ import annotations

import csv
import io
import re

from backend.app.core.settings import UPLOAD_DIR
from backend.app.db.database import get_db
from backend.app.db.exams import (
    active_job,
    create_evaluator_config,
    create_exam,
    delete_exam,
    exam_results,
    exam_scripts,
    get_exam,
    latest_job,
    list_evaluator_configs,
    list_exams,
    request_cancel,
    update_exam,
)
from backend.app.models.schemas import EvaluatorConfigCreate, ExamCreate, ExamUpdate, GradeRequest
from backend.app.services.evaluation.exam_jobs import (
    JobConflict,
    NothingToDo,
    SetupRequired,
    start_grade_job,
    start_process_job,
)
from backend.app.services.storage import delete_upload
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/exams", tags=["exams"])


def _require_exam(conn, exam_id: str) -> dict:
    exam = get_exam(conn, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.get("")
def list_all_exams() -> dict[str, object]:
    with get_db() as conn:
        return {"exams": list_exams(conn)}


@router.post("")
def create_new_exam(body: ExamCreate) -> dict[str, object]:
    return create_exam(body.name)


@router.get("/evaluator-configs")
def list_exam_evaluator_configs() -> dict[str, object]:
    with get_db() as conn:
        return {"evaluator_configs": list_evaluator_configs(conn)}


@router.post("/evaluator-configs")
def create_exam_evaluator_config(body: EvaluatorConfigCreate) -> dict[str, object]:
    return create_evaluator_config(body.model_dump())


@router.get("/{exam_id}")
def get_exam_detail(exam_id: str) -> dict[str, object]:
    with get_db() as conn:
        return _require_exam(conn, exam_id)


@router.delete("/{exam_id}")
def delete_exam_endpoint(exam_id: str) -> dict[str, str]:
    with get_db() as conn:
        _require_exam(conn, exam_id)
        if active_job(conn, exam_id):
            raise HTTPException(
                status_code=409,
                detail="A job is still running for this exam. Cancel it before deleting.",
            )

    script_ids = delete_exam(exam_id)
    if script_ids is None:
        raise HTTPException(status_code=404, detail="Exam not found")

    for script_id in script_ids:
        for leftover in UPLOAD_DIR.glob(f"{script_id}.*"):
            delete_upload(script_id, leftover)

    return {"deleted": exam_id}


@router.put("/{exam_id}")
def update_exam_detail(exam_id: str, body: ExamUpdate) -> dict[str, object]:
    questions = None
    if body.questions is not None:
        questions = []
        for q in body.questions:
            item = q.model_dump()
            item["question_id"] = item["question_id"].strip()
            questions.append(item)
    exam = update_exam(
        exam_id,
        name=body.name,
        questions=questions,
        review_confidence_threshold=body.review_confidence_threshold,
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.get("/{exam_id}/scripts")
def list_exam_scripts(exam_id: str) -> dict[str, object]:
    with get_db() as conn:
        _require_exam(conn, exam_id)
        return {"scripts": exam_scripts(conn, exam_id)}


@router.get("/{exam_id}/results")
def get_exam_results(exam_id: str) -> dict[str, object]:
    with get_db() as conn:
        return exam_results(conn, _require_exam(conn, exam_id))


def _csv_safe(value: object) -> object:
    """Stop spreadsheet apps from running a student name like '=1+1' as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text and text[0] in "=+-@\t\r" else text


@router.get("/{exam_id}/export.csv")
def export_results(exam_id: str) -> Response:
    with get_db() as conn:
        exam = _require_exam(conn, exam_id)
        results = exam_results(conn, exam)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    qids = results["question_ids"]
    writer.writerow(["Student", "Student ID", *qids, "Total", "Max", "Percent", "Pending review"])
    for row in results["rows"]:
        graded = row["graded_questions"] > 0  # an ungraded/failed paper shouldn't show as 0%
        writer.writerow(
            [
                _csv_safe(row["student_name"]),
                _csv_safe(row["student_id"]),
                *[row["marks"].get(q, {}).get("awarded", "") for q in qids],
                row["total_awarded"] if graded else "",
                row["total_max"],
                row["percentage"] if graded and row["percentage"] is not None else "",
                row["needs_review_count"],
            ]
        )

    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", exam["name"]).strip("_") or "exam"
    return Response(
        content="\ufeff" + buffer.getvalue(),  # BOM so Excel reads UTF-8 names correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}_results.csv"'},
    )


# ── Jobs ─────────────────────────────────────────────────────────────────────


def _start(starter, *args) -> dict[str, object]:
    try:
        return {"job": starter(*args)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SetupRequired, NothingToDo) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{exam_id}/process")
def process_exam_scripts(exam_id: str) -> dict[str, object]:
    """Segment and transcribe every uploaded script that hasn't been processed yet."""
    return _start(start_process_job, exam_id)


@router.post("/{exam_id}/grade")
def grade_exam(exam_id: str, body: GradeRequest | None = None) -> dict[str, object]:
    """Grade every student's answers against the exam's answer key."""
    return _start(start_grade_job, exam_id, (body or GradeRequest()).regrade)


@router.get("/{exam_id}/job")
def get_latest_job(exam_id: str) -> dict[str, object]:
    with get_db() as conn:
        _require_exam(conn, exam_id)
        return {"job": latest_job(conn, exam_id)}


@router.post("/{exam_id}/job/cancel")
def cancel_job(exam_id: str) -> dict[str, object]:
    return {"cancelling": request_cancel(exam_id)}
