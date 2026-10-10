"""Grading of single answers.

``AnswerGrader`` turns one (question, answer) pair into a ``GradeResult`` without touching
storage. ``GradingService`` is the one place such a result becomes a stored grade, so a grade is
built, snapped to the exam's mark step, flagged and persisted the same way wherever it comes
from (the background job runner, a test). The batch evaluator uses ``AnswerGrader`` directly:
it writes JSON, not the database, and is deliberately unaffected by the exam-level rules in
``GradingService``.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from backend.app.db.repositories import EvaluationRepository
from backend.app.extraction.segment import normalize_question_id
from backend.app.grading.evaluators import EvaluationEngine
from backend.app.models.domain import GradeResult, QuestionSpec, ReviewPolicy

log = logging.getLogger(__name__)

NONBLANK_ZERO_FLAG = "A non-blank answer was scored 0 marks."


def lookup_answer(answers: dict[str, str], question_id: str) -> str:
    """Find a question's answer whichever way its id was spelled (Q3, 3, q03)."""
    direct = answers.get(question_id)
    if direct:
        return direct
    norm = normalize_question_id(question_id)
    return answers.get(f"Q{norm}", "") or answers.get(norm, "")


def snap_marks(awarded: float, step: float, max_marks: float) -> float:
    """Round marks to the nearest multiple of ``step`` (halves round up), within 0..max."""
    if step <= 0:
        return awarded
    snapped = math.floor(awarded / step + 0.5) * step
    return round(max(0.0, min(snapped, max_marks)), 2)


def rule_flags(spec: QuestionSpec, answer: str, awarded: float) -> list[str]:
    """Reasons to send an answer to review that do not depend on model confidence. A
    descriptive answer that was written but earned nothing is the model's most costly miss, so
    a teacher should see it however sure the model was. (A wrong objective answer is normally
    0 marks, so that case is not flagged.)"""
    if not spec.is_objective and answer.strip() and awarded <= 0:
        return [NONBLANK_ZERO_FLAG]
    return []


class AnswerGrader:
    """Builds a GradeResult for one answer. No persistence."""

    def __init__(self, engine: EvaluationEngine | None = None) -> None:
        self.engine = engine or EvaluationEngine()

    @staticmethod
    def threshold_for(spec: QuestionSpec, exam_threshold: float) -> float:
        """The question's own review threshold, else the exam's."""
        if spec.review_confidence_threshold is not None:
            return ReviewPolicy.clamp(spec.review_confidence_threshold)
        return exam_threshold

    def grade(
        self,
        spec: QuestionSpec,
        answer: str,
        threshold: float,
        page_number: int | None = None,
    ) -> GradeResult:
        base: dict[str, Any] = {
            "question_id": spec.question_id,
            "question_text": spec.question_text,
            "question_type": spec.question_type,
            "page_number": page_number,
            "answer_text": answer,
            "golden_answer": spec.golden_answer,
            "rubric": spec.criteria,
            "max_marks": spec.max_marks,
        }
        if not answer.strip():
            # A skipped question is the expected, common case and there is nothing to grade:
            # score 0 with no model call and keep it out of the review queue. confidence=1.0 is
            # certainty in the 0-mark decision itself, and it has to clear the threshold because
            # review status is re-derived from confidence whenever a grade is read.
            return GradeResult(
                **base,
                awarded_marks=0.0,
                confidence=1.0,
                needs_review=False,
                flag_reasons=[],
                evidence=[],
                reasoning="No answer was attempted, so 0 marks were awarded without model grading.",
                llm_reasoning=None,
                llm_model=None,
                status="scored",
                evaluator_snapshot={
                    "evaluator": "rule",
                    "rule": "blank_answer",
                    "question_type": spec.question_type,
                },
            )

        evaluation = self.engine.evaluate(spec, answer)
        awarded = max(0.0, min(round(evaluation.awarded_marks, 2), spec.max_marks))
        # needs_review comes from confidence alone, never a model-filled flag.
        low, reason = ReviewPolicy(threshold).verdict(evaluation.confidence)
        return GradeResult(
            **base,
            awarded_marks=awarded,
            confidence=evaluation.confidence,
            needs_review=low,
            flag_reasons=[reason.rstrip(".")] if low else [],
            evidence=evaluation.evidence,
            reasoning=evaluation.reasoning,
            llm_reasoning=evaluation.llm_reasoning,
            llm_model=evaluation.model,
            status="needs_review" if low else "scored",
            criteria_scores=evaluation.criteria_scores,
            evaluator_snapshot=evaluation.snapshot,
        )

    @staticmethod
    def failed(
        spec: QuestionSpec, answer: str, reason: str, page_number: int | None = None
    ) -> GradeResult:
        """A zero-confidence result that sends an answer to the teacher when grading broke."""
        return GradeResult(
            question_id=spec.question_id,
            question_text=spec.question_text,
            question_type=spec.question_type,
            page_number=page_number,
            answer_text=answer,
            golden_answer=spec.golden_answer,
            rubric=spec.criteria,
            max_marks=spec.max_marks,
            awarded_marks=0.0,
            confidence=0.0,
            needs_review=True,
            flag_reasons=[reason],
            evidence=[],
            reasoning=f"{reason}. Please grade manually.",
            llm_reasoning=None,
            llm_model=None,
            status="needs_review",
        )


class GradingService:
    def __init__(self, answers: AnswerGrader, evaluations: EvaluationRepository) -> None:
        self.answers = answers
        self.evaluations = evaluations

    def grade_and_store(
        self,
        script_id: str,
        spec: QuestionSpec,
        answer: str,
        exam_threshold: float,
        page_number: int | None = None,
        *,
        mark_step: float = 1.0,
        job_id: str | None = None,
    ) -> GradeResult:
        """Grade one answer and store it. A failure raises before anything is written, so an
        existing grade is never replaced by a placeholder; a teacher-approved answer is never
        replaced at all (the repository refuses).

        Descriptive answers are snapped to the exam's ``mark_step`` (whole marks by default).
        """
        threshold = self.answers.threshold_for(spec, exam_threshold)
        result = self.answers.grade(spec, answer, threshold, page_number)
        if not spec.is_objective and answer.strip():
            result.awarded_marks = snap_marks(result.awarded_marks, mark_step, spec.max_marks)
        flags = rule_flags(spec, answer, result.awarded_marks)
        if flags:
            result.needs_review = True
            result.status = "needs_review"
            result.flag_reasons = [*result.flag_reasons, *flags]
        self.evaluations.upsert(script_id, spec, result, rule_flags=flags, job_id=job_id)
        return result
