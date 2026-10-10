"""Grading of single answers and whole scripts.

``AnswerGrader`` turns one (question, answer) pair into a ``GradeResult`` without touching
storage. ``GradingService`` is the one place such a result becomes a stored grade; the
background job runner and the per-script endpoint go through it, so a grade is built,
thresholded and persisted the same way everywhere. The batch evaluator uses ``AnswerGrader``
directly (it writes JSON, not the database).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.db.exams import ExamRepository
from backend.app.db.repositories import (
    EvaluationRepository,
    ExtractionRepository,
    ScriptRepository,
    StudentRepository,
)
from backend.app.extraction.reader import ScriptReader
from backend.app.extraction.segment import normalize_question_id
from backend.app.grading.evaluators import EvaluationEngine
from backend.app.grading.file_store import EvaluationFileStore
from backend.app.models.domain import GradeResult, QuestionSpec, ReviewPolicy

log = logging.getLogger(__name__)


def lookup_answer(answers: dict[str, str], question_id: str) -> str:
    """Find a question's answer whichever way its id was spelled (Q3, 3, q03)."""
    direct = answers.get(question_id)
    if direct:
        return direct
    norm = normalize_question_id(question_id)
    return answers.get(f"Q{norm}", "") or answers.get(norm, "")


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
    def __init__(
        self,
        config: Settings,
        answers: AnswerGrader,
        evaluations: EvaluationRepository,
        extractions: ExtractionRepository,
        scripts: ScriptRepository,
        students: StudentRepository,
        exams: ExamRepository,
        reader: ScriptReader,
        file_store: EvaluationFileStore,
    ) -> None:
        self.config = config
        self.answers = answers
        self.evaluations = evaluations
        self.extractions = extractions
        self.scripts = scripts
        self.students = students
        self.exams = exams
        self.reader = reader
        self.file_store = file_store

    def grade_and_store(
        self,
        script_id: str,
        spec: QuestionSpec,
        answer: str,
        exam_threshold: float,
        page_number: int | None = None,
    ) -> GradeResult:
        """Grade one answer and store it. A failure raises before anything is written, so an
        existing grade is never replaced by a placeholder; a teacher-approved answer is never
        replaced at all (the repository refuses)."""
        threshold = self.answers.threshold_for(spec, exam_threshold)
        result = self.answers.grade(spec, answer, threshold, page_number)
        self.evaluations.upsert(script_id, spec, result, threshold)
        return result

    def grade_script(
        self, script_id: str, script_path: Path, specs: list[QuestionSpec]
    ) -> dict[str, Any]:
        """Grade every question of one script against the given rubric configuration, store
        the grades and write the JSON evaluation file."""
        if not self.scripts.get(script_id):
            self.students.upsert(script_id, script_id)
            self.scripts.insert(script_id, script_id, script_path.name, str(script_path))
        exam_threshold = self.exams.threshold_for_script(script_id)

        answers, pages = self.extractions.answers_for_script(script_id)
        if not answers or any(not lookup_answer(answers, s.question_id) for s in specs):
            reading = self.reader.read(script_path, self.config.page_image_dir / script_id)
            answers.update(reading.questions)
            pages.update(reading.question_pages)

        for spec in specs:
            self.grade_and_store(
                script_id,
                spec,
                lookup_answer(answers, spec.question_id),
                exam_threshold,
                pages.get(spec.question_id),
            )

        # Report what is actually stored: that includes any teacher-approved marks the
        # re-grade left untouched.
        stored = self.evaluations.for_script(script_id)
        needs_review = sum(e["status"] == "needs_review" for e in stored)
        payload = {
            "script_id": script_id,
            "results": stored,
            "total_awarded_marks": round(sum(e["awarded_marks"] for e in stored), 2),
            "total_max_marks": round(sum(e["max_marks"] for e in stored), 2),
            "needs_review": needs_review,
        }
        self.scripts.set_status(script_id, "needs_review" if needs_review else "graded")
        self.file_store.save(script_id, payload)
        return payload
