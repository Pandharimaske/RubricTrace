"""Plain domain objects shared by extraction, grading, persistence and the API layer.

Nothing in this module imports from the rest of the app, so every layer can depend on it
without creating import cycles.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

QUESTION_TYPES = ("mcq", "true_false", "short_answer", "long_answer")
OBJECTIVE_TYPES = frozenset({"mcq", "true_false"})

_OPTION_LINE = re.compile(r"(?m)^[ \t]*[A-Za-z][.)][ \t]*\S")


def normalize_question_type(raw: str | None) -> str:
    """Map the many spellings seen in manifests/UI ("MCQ", "true/false", "Short answer")
    onto the canonical names in QUESTION_TYPES. Unknown or empty values pass through
    lower-cased so callers can still tell "unspecified" from a real type."""
    value = re.sub(r"[^a-z]", "", (raw or "").lower())
    aliases = {
        "mcq": "mcq",
        "multiplechoice": "mcq",
        "truefalse": "true_false",
        "tf": "true_false",
        "trueorfalse": "true_false",
        "shortanswer": "short_answer",
        "short": "short_answer",
        "longanswer": "long_answer",
        "long": "long_answer",
    }
    return aliases.get(value, value)


class ReviewPolicy:
    """Decides which answers a teacher must look at.

    Confidence is stored per answer; whether it "needs review" is decided when it is READ,
    by comparing it with a threshold. The threshold is configuration (the question's own
    override, else the exam's), so changing it never needs a re-grade. A teacher-approved
    answer is never flagged again, whatever its confidence.
    """

    DEFAULT_THRESHOLD = 0.65
    MIN_THRESHOLD = 0.05
    MAX_THRESHOLD = 1.0

    # Reasons derived from confidence when an answer was graded. They depend on the
    # threshold, so they are rebuilt at read time instead of trusted.
    _CONFIDENCE_REASON_PREFIXES = (
        "Very low confidence",
        "Low confidence",
        "LLM flagged for review",
    )

    def __init__(self, threshold: float | None = None) -> None:
        self.threshold = self.clamp(threshold) if threshold is not None else self.DEFAULT_THRESHOLD

    @classmethod
    def clamp(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return cls.DEFAULT_THRESHOLD
        return round(max(cls.MIN_THRESHOLD, min(cls.MAX_THRESHOLD, number)), 2)

    def verdict(self, confidence: float | None) -> tuple[bool, str]:
        """(needs_review, reason) for one confidence under this threshold."""
        conf = confidence or 0.0
        if conf < self.threshold:
            return (
                True,
                f"Low confidence ({conf:.2f}) is below the review threshold "
                f"({self.threshold:.2f}).",
            )
        return False, ""

    def effective_status(self, status: str, confidence: float | None) -> str:
        """Status as the teacher should see it under this threshold."""
        if status == "teacher_approved":
            return status
        return "needs_review" if (confidence or 0.0) < self.threshold else "scored"

    def flag_reasons(
        self, stored: list[str] | None, confidence: float | None, status: str
    ) -> list[str]:
        """Grade-time reasons that don't depend on confidence, plus a live confidence reason."""
        reasons = [r for r in (stored or []) if not r.startswith(self._CONFIDENCE_REASON_PREFIXES)]
        conf = confidence or 0.0
        if status != "teacher_approved" and conf < self.threshold:
            reasons.append(
                f"Confidence {conf:.0%} is below the review threshold ({self.threshold:.0%})"
            )
        return reasons


DEFAULT_REVIEW_THRESHOLD = ReviewPolicy.DEFAULT_THRESHOLD


@dataclass(frozen=True)
class QuestionSpec:
    """Everything needed to grade one answer to one question."""

    question_id: str
    question_text: str = ""
    question_type: str = ""
    golden_answer: str = ""
    max_marks: float = 1.0
    criteria: list[dict[str, Any]] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    guidance: str = ""
    review_confidence_threshold: float | None = None
    evaluator_config_id: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> QuestionSpec:
        """Build from an exam question row, a request body or a dataset record."""
        criteria = list(data.get("criteria") or data.get("rubric") or [])
        guidance = data.get("guidance") or " ".join(
            str(c["guidance"]) for c in criteria if c.get("guidance")
        )
        return cls(
            question_id=str(data["question_id"]),
            question_text=str(data.get("question_text") or ""),
            question_type=normalize_question_type(data.get("question_type")),
            golden_answer=str(data.get("golden_answer") or ""),
            max_marks=float(data["max_marks"]),
            criteria=criteria,
            options=list(data.get("options") or []),
            guidance=str(guidance),
            review_confidence_threshold=data.get("review_confidence_threshold"),
            evaluator_config_id=data.get("evaluator_config_id"),
        )

    @property
    def is_objective(self) -> bool:
        return self.question_type in OBJECTIVE_TYPES

    def display_text(self) -> str:
        """The question as a model should see it: option lines included when they are
        stored separately from the question text."""
        if not self.options or _OPTION_LINE.search(self.question_text):
            return self.question_text
        lines = "\n".join(f"{o['option_key']}. {o['option_text']}" for o in self.options)
        return f"{self.question_text}\n{lines}".strip()


@dataclass
class GradeResult:
    """The outcome of grading one answer. `to_dict()` is the shape stored in JSON
    evaluation files, returned by the API and written by the batch pipeline."""

    question_id: str
    question_text: str
    question_type: str
    page_number: int | None
    answer_text: str
    golden_answer: str
    rubric: list[dict[str, Any]]
    max_marks: float
    awarded_marks: float
    confidence: float
    needs_review: bool
    flag_reasons: list[str]
    evidence: list[str]
    reasoning: str
    llm_reasoning: dict[str, Any] | None
    llm_model: str | None
    status: str
    criteria_scores: list[dict[str, Any]] = field(default_factory=list)
    evaluator_snapshot: dict[str, Any] | None = None
    teacher_override: float | None = None
    teacher_override_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
