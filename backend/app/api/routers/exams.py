"""Exam endpoints: authoring, scripts, grading jobs, and results."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from typing import Any

from backend.app.api.deps import ContainerDep
from backend.app.container import Container
from backend.app.grading.jobs import JobConflict, NothingToDo, SetupRequired
from backend.app.models.schemas import EvaluatorConfigCreate, ExamCreate, ExamUpdate, GradeRequest
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/exams", tags=["exams"])


def _require_exam(c: Container, exam_id: str) -> dict[str, Any]:
    exam = c.exams.get(exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.get("")
def list_all_exams(c: ContainerDep) -> dict[str, object]:
    return {"exams": c.exams.list_all()}


@router.post("")
def create_new_exam(body: ExamCreate, c: ContainerDep) -> dict[str, object]:
    return c.exams.create(body.name)


@router.get("/evaluator-configs")
def list_exam_evaluator_configs(c: ContainerDep) -> dict[str, object]:
    return {"evaluator_configs": c.evaluator_configs.list_all()}


@router.post("/evaluator-configs")
def create_exam_evaluator_config(body: EvaluatorConfigCreate, c: ContainerDep) -> dict[str, object]:
    return c.evaluator_configs.create(body.model_dump())


@router.get("/{exam_id}")
def get_exam_detail(exam_id: str, c: ContainerDep) -> dict[str, object]:
    return _require_exam(c, exam_id)


@router.delete("/{exam_id}")
def delete_exam_endpoint(exam_id: str, c: ContainerDep) -> dict[str, str]:
    _require_exam(c, exam_id)
    if c.job_records.active(exam_id):
        raise HTTPException(
            status_code=409,
            detail="A job is still running for this exam. Cancel it before deleting.",
        )

    script_ids = c.exams.delete(exam_id)
    if script_ids is None:
        raise HTTPException(status_code=404, detail="Exam not found")
    for script_id in script_ids:
        c.storage.delete(script_id)
    return {"deleted": exam_id}


@router.put("/{exam_id}")
def update_exam_detail(exam_id: str, body: ExamUpdate, c: ContainerDep) -> dict[str, object]:
    questions = None
    if body.questions is not None:
        questions = []
        for q in body.questions:
            item = q.model_dump()
            item["question_id"] = item["question_id"].strip()
            questions.append(item)
    exam = c.exams.update(
        exam_id,
        name=body.name,
        questions=questions,
        review_confidence_threshold=body.review_confidence_threshold,
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.get("/{exam_id}/scripts")
def list_exam_scripts(exam_id: str, c: ContainerDep) -> dict[str, object]:
    _require_exam(c, exam_id)
    return {"scripts": c.exams.scripts(exam_id)}


@router.get("/{exam_id}/results")
def get_exam_results(exam_id: str, c: ContainerDep) -> dict[str, object]:
    return c.exams.results(_require_exam(c, exam_id))


def _csv_safe(value: object) -> object:
    """Stop spreadsheet apps from running a student name like '=1+1' as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text and text[0] in "=+-@\t\r" else text


@router.get("/{exam_id}/export.csv")
def export_results(exam_id: str, c: ContainerDep) -> Response:
    exam = _require_exam(c, exam_id)
    results = c.exams.results(exam)

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


def _start(starter: Callable[..., dict[str, Any]], *args: object) -> dict[str, object]:
    try:
        return {"job": starter(*args)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SetupRequired, NothingToDo) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{exam_id}/process")
def process_exam_scripts(exam_id: str, c: ContainerDep) -> dict[str, object]:
    """Segment and transcribe every uploaded script that hasn't been processed yet."""
    return _start(c.jobs.start_process, exam_id)


@router.post("/{exam_id}/grade")
def grade_exam(
    exam_id: str, c: ContainerDep, body: GradeRequest | None = None
) -> dict[str, object]:
    """Grade every student's answers against the exam's answer key."""
    return _start(c.jobs.start_grade, exam_id, (body or GradeRequest()).regrade)


@router.get("/{exam_id}/job")
def get_latest_job(exam_id: str, c: ContainerDep) -> dict[str, object]:
    _require_exam(c, exam_id)
    return {"job": c.job_records.latest(exam_id)}


@router.post("/{exam_id}/job/cancel")
def cancel_job(exam_id: str, c: ContainerDep) -> dict[str, object]:
    return {"cancelling": c.job_records.request_cancel(exam_id)}
