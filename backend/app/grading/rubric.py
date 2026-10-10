"""Map LLM grading output onto the public ScoreResponse schema."""

from backend.app.grading.grader import LLMGrader
from backend.app.models.domain import ReviewPolicy
from backend.app.models.schemas import CriterionScore, ScoreRequest, ScoreResponse


class RubricScorer:
    """Service that evaluates question criteria against student answers using an LLM grader."""

    def __init__(self, grader: LLMGrader | None = None) -> None:
        self.grader = grader or LLMGrader()

    def score(self, request: ScoreRequest) -> ScoreResponse:
        rubric = [criterion.model_dump() for criterion in request.criteria]
        result, model = self.grader.grade(
            question_id=request.question_id,
            student_answer=request.student_answer,
            golden_answer="",
            rubric=rubric,
            max_marks=request.max_marks,
        )

        satisfied = {name.lower() for name in result.get("criteria_satisfied", [])}
        partial = {name.lower() for name in result.get("criteria_partial", [])}
        failed = {name.lower() for name in result.get("criteria_failed", [])}

        criterion_scores: list[CriterionScore] = []
        for criterion in request.criteria:
            key = criterion.name.lower()
            if key in satisfied:
                awarded = criterion.marks
            elif key in partial:
                awarded = round(criterion.marks * 0.5, 2)
            elif key in failed:
                awarded = 0.0
            else:
                awarded = 0.0
            criterion_scores.append(
                CriterionScore(
                    name=criterion.name,
                    awarded_marks=awarded,
                    max_marks=criterion.marks,
                    matched_concepts=result.get("concepts_found", []),
                    missing_concepts=result.get("concepts_missing", []),
                    evidence=result.get("reasoning", ""),
                    confidence=result["confidence"],
                )
            )

        # Standalone scoring has no exam, so it uses the default review threshold.
        needs_review, review_reason = ReviewPolicy().verdict(result["confidence"])
        result = {**result, "needs_review": needs_review, "review_reason": review_reason}

        return ScoreResponse(
            question_id=request.question_id,
            awarded_marks=result["awarded_marks"],
            max_marks=request.max_marks,
            confidence=result["confidence"],
            needs_review=needs_review,
            criteria=criterion_scores,
            llm_explanation=result,
            llm_model=model,
        )
