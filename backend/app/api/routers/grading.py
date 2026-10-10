"""Question scoring, grade retrieval, and teacher overrides."""

from typing import Any

from backend.app.api.deps import ContainerDep
from backend.app.container import Container
from backend.app.llm.errors import ModelUnavailable
from backend.app.models.schemas import ScoreRequest, ScoreResponse, TeacherOverrideRequest
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["grading"])


def _evaluation_payload(c: Container, script_id: str) -> dict[str, Any]:
    """Stored grades for a script, with totals."""
    if not c.scripts.get(script_id):
        raise HTTPException(status_code=404, detail="Script not found")
    evaluations = c.evaluations.for_script(script_id)
    totals = c.evaluations.totals_for_script(script_id)
    return {
        "script_id": script_id,
        "results": evaluations,
        "total_awarded_marks": totals.get("total_awarded", 0),
        "total_max_marks": totals.get("total_max", 0),
        "needs_review": totals.get("needs_review_count", 0),
    }


@router.get("/scripts/{script_id}/evaluation")
def get_evaluation(script_id: str, c: ContainerDep) -> dict[str, object]:
    return _evaluation_payload(c, script_id)


@router.post("/scripts/{script_id}/override")
def override_score(
    script_id: str, request: TeacherOverrideRequest, c: ContainerDep
) -> dict[str, object]:
    """Record the teacher's mark for one answer. The AI's own mark is kept alongside it."""
    stored = c.evaluations.for_script(script_id)
    matching = [item for item in stored if item["question_id"] == request.question_id]
    if not matching:
        raise HTTPException(status_code=404, detail="Question evaluation not found")
    if request.awarded_marks > matching[0]["max_marks"]:
        raise HTTPException(status_code=400, detail="Override exceeds question maximum")
    if not c.evaluations.apply_override(
        script_id, request.question_id, request.awarded_marks, request.reason
    ):
        raise HTTPException(status_code=500, detail="Override failed")
    return _evaluation_payload(c, script_id)


@router.post("/score", response_model=ScoreResponse)
def score(request: ScoreRequest, c: ContainerDep) -> ScoreResponse:
    try:
        return c.rubric_scorer.score(request)
    except ModelUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM grading is required and unavailable: {exc}"
        ) from exc
