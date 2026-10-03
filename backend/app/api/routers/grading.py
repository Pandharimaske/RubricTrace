"""Question scoring, evaluation persistence, and teacher overrides."""

from backend.app.core.settings import UPLOAD_DIR
from backend.app.db.database import (
    apply_override,
    get_db,
    get_evaluations_for_script,
    get_script_totals,
)
from backend.app.models.schemas import (
    ScoreRequest,
    ScoreResponse,
    TeacherEvaluationRequest,
    TeacherOverrideRequest,
)
from backend.app.services.assessment.grading.rubric import score_answer
from backend.app.services.assessment.jobs.pipeline import evaluate_teacher_config
from backend.app.services.assessment.jobs.repository import load_evaluation, save_evaluation
from backend.app.services.assessment.llm.ollama import ModelUnavailable
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["grading"])


@router.post("/scripts/{script_id}/evaluate")
def evaluate_script(script_id: str, request: TeacherEvaluationRequest) -> dict[str, object]:
    if request.script_id != script_id:
        raise HTTPException(status_code=400, detail="script_id does not match request")
    matches = list(UPLOAD_DIR.glob(f"{script_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Script not found")
    try:
        return evaluate_teacher_config(request, matches[0], script_id, use_db=True)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM grading is required and unavailable: {exc}"
        ) from exc


@router.get("/scripts/{script_id}/evaluation")
def get_evaluation(script_id: str) -> dict[str, object]:
    with get_db() as conn:
        evaluations = get_evaluations_for_script(conn, script_id)
        totals = get_script_totals(conn, script_id)
    if not evaluations:
        try:
            return load_evaluation(script_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "script_id": script_id,
        "results": evaluations,
        "total_awarded_marks": totals.get("total_awarded", 0),
        "total_max_marks": totals.get("total_max", 0),
        "needs_review": totals.get("needs_review_count", 0),
    }


@router.post("/scripts/{script_id}/override")
def override_score(script_id: str, request: TeacherOverrideRequest) -> dict[str, object]:
    with get_db() as conn:
        evaluations = get_evaluations_for_script(conn, script_id)
    matching = [item for item in evaluations if item["question_id"] == request.question_id]
    if not matching:
        try:
            payload = load_evaluation(script_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        matching_json = [
            item for item in payload["results"] if item["question_id"] == request.question_id
        ]
        if not matching_json:
            raise HTTPException(status_code=404, detail="Question evaluation not found")
        result = matching_json[0]
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
        save_evaluation(script_id, payload)
        return payload

    evaluation = matching[0]
    if request.awarded_marks > evaluation["max_marks"]:
        raise HTTPException(status_code=400, detail="Override exceeds question maximum")
    if not apply_override(script_id, request.question_id, request.awarded_marks, request.reason):
        raise HTTPException(status_code=500, detail="Override failed")
    return get_evaluation(script_id)


@router.post("/score", response_model=ScoreResponse)
def score(request: ScoreRequest) -> ScoreResponse:
    try:
        return score_answer(request)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM grading is required and unavailable: {exc}"
        ) from exc
