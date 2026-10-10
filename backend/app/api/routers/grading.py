"""Question scoring, evaluation persistence, and teacher overrides."""

from pathlib import Path
from typing import Any

from backend.app.api.deps import ContainerDep
from backend.app.container import Container
from backend.app.llm.errors import ModelUnavailable
from backend.app.models.domain import QuestionSpec
from backend.app.models.schemas import (
    ScoreRequest,
    ScoreResponse,
    TeacherEvaluationRequest,
    TeacherOverrideRequest,
)
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["grading"])


def _evaluation_payload(c: Container, script_id: str) -> dict[str, Any]:
    """Stored evaluations for a script, falling back to the legacy JSON file."""
    evaluations = c.evaluations.for_script(script_id)
    if not evaluations:
        try:
            return c.file_store.load(script_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    totals = c.evaluations.totals_for_script(script_id)
    return {
        "script_id": script_id,
        "results": evaluations,
        "total_awarded_marks": totals.get("total_awarded", 0),
        "total_max_marks": totals.get("total_max", 0),
        "needs_review": totals.get("needs_review_count", 0),
    }


@router.post("/scripts/{script_id}/evaluate")
def evaluate_script(
    script_id: str, request: TeacherEvaluationRequest, c: ContainerDep
) -> dict[str, object]:
    if request.script_id != script_id:
        raise HTTPException(status_code=400, detail="script_id does not match request")
    script = c.scripts.get(script_id)
    if not script or not Path(script["file_path"]).is_file():
        raise HTTPException(status_code=404, detail="Script not found")
    specs = [QuestionSpec.from_mapping(q.model_dump()) for q in request.questions]
    try:
        return c.grading.grade_script(script_id, Path(script["file_path"]), specs)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM grading is required and unavailable: {exc}"
        ) from exc


@router.get("/scripts/{script_id}/evaluation")
def get_evaluation(script_id: str, c: ContainerDep) -> dict[str, object]:
    return _evaluation_payload(c, script_id)


@router.post("/scripts/{script_id}/override")
def override_score(
    script_id: str, request: TeacherOverrideRequest, c: ContainerDep
) -> dict[str, object]:
    stored = c.evaluations.for_script(script_id)
    matching = [item for item in stored if item["question_id"] == request.question_id]

    if not matching:  # legacy script graded outside an exam: only a JSON file exists
        try:
            payload = c.file_store.load(script_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        in_file = [r for r in payload["results"] if r["question_id"] == request.question_id]
        if not in_file:
            raise HTTPException(status_code=404, detail="Question evaluation not found")
        result = in_file[0]
        if request.awarded_marks > result["max_marks"]:
            raise HTTPException(status_code=400, detail="Override exceeds question maximum")
        result.update(
            awarded_marks=request.awarded_marks,
            teacher_override=request.awarded_marks,
            teacher_override_reason=request.reason,
            status="teacher_approved",
        )
        payload["total_awarded_marks"] = round(
            sum(item["awarded_marks"] for item in payload["results"]), 2
        )
        payload["needs_review"] = sum(
            item["status"] == "needs_review" for item in payload["results"]
        )
        c.file_store.save(script_id, payload)
        return payload

    if request.awarded_marks > matching[0]["max_marks"]:
        raise HTTPException(status_code=400, detail="Override exceeds question maximum")
    if not c.evaluations.apply_override(
        script_id, request.question_id, request.awarded_marks, request.reason
    ):
        raise HTTPException(status_code=500, detail="Override failed")
    c.exams.refresh_script_status(script_id)
    return _evaluation_payload(c, script_id)


@router.post("/score", response_model=ScoreResponse)
def score(request: ScoreRequest, c: ContainerDep) -> ScoreResponse:
    try:
        return c.rubric_scorer.score(request)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM grading is required and unavailable: {exc}"
        ) from exc
