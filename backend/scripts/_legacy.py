"""Function-style entry points the one-off evaluation scripts were written against.

The application itself is class-based (LLMGrader, ReviewPolicy); these thin wrappers keep the
benchmark scripts' call sites unchanged. They are for scripts/ only -- nothing in app/ imports
this module.
"""

from __future__ import annotations

from typing import Any

from backend.app.grading.grader import LLMGrader
from backend.app.llm.provider import ModelClient
from backend.app.models.domain import DEFAULT_REVIEW_THRESHOLD, ReviewPolicy

__all__ = [
    "DEFAULT_REVIEW_THRESHOLD",
    "clamp_review_threshold",
    "llm_grade",
    "llm_grade_mcq",
    "review_verdict",
]

clamp_review_threshold = ReviewPolicy.clamp


def review_verdict(confidence: float | None, threshold: float) -> tuple[bool, str]:
    return ReviewPolicy(threshold).verdict(confidence)


def llm_grade(
    question_id: str,
    student_answer: str,
    golden_answer: str,
    rubric: list[dict[str, Any]],
    max_marks: float,
    guidance: str = "",
    question_text: str = "",
    question_type: str = "",
    client: ModelClient | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    return LLMGrader(client=client, model=model).grade(
        question_id=question_id,
        student_answer=student_answer,
        golden_answer=golden_answer,
        rubric=rubric,
        max_marks=max_marks,
        guidance=guidance,
        question_text=question_text,
        question_type=question_type,
    )


def llm_grade_mcq(
    question_id: str,
    student_answer: str,
    golden_answer: str,
    question_text: str = "",
    client: ModelClient | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    return LLMGrader(client=client, model=model).grade_mcq(
        question_id=question_id,
        student_answer=student_answer,
        golden_answer=golden_answer,
        question_text=question_text,
    )
