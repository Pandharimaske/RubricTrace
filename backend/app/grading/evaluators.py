"""Evaluators turn (question spec, student answer) into marks.

* ``ChoiceEvaluator``   deterministic: compares the marked option with the answer key for
                         MCQ / true-false questions. No model call, full confidence. It returns
                         ``None`` when it cannot tell what the student chose, so a model can
                         decide instead.
* ``LLMEvaluator``       grades with the LLM: the lean verdict-only prompt for objective
                         questions, the full rubric prompt for descriptive ones.
* ``EvaluationEngine``   routes each question to the right evaluator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.app.grading.grader import LLMGrader
from backend.app.models.domain import QuestionSpec

# A lone key, optionally wrapped or followed by punctuation: "B", "(B)", "B.", "B) text".
# A key followed only by a space ("A lot of ...") is prose, not a choice.
_LEADING_TOKEN = re.compile(r"^\(?([A-Za-z0-9]+)(?:[.):\-]|$)")
_TRUE = frozenset({"true", "t", "yes", "y"})
_FALSE = frozenset({"false", "f", "no", "n"})


@dataclass
class Evaluation:
    awarded_marks: float
    confidence: float
    reasoning: str = ""
    evidence: list[str] = field(default_factory=list)
    criteria_scores: list[dict[str, Any]] = field(default_factory=list)
    llm_reasoning: dict[str, Any] | None = None
    model: str | None = None
    snapshot: dict[str, Any] | None = None


class Evaluator(Protocol):
    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation | None: ...


def _true_false(text: str) -> str | None:
    token = text.strip().strip(".!").lower()
    if token in _TRUE:
        return "true"
    if token in _FALSE:
        return "false"
    return None


def correct_option_key(spec: QuestionSpec) -> str | None:
    """The key of the correct option: the one flagged is_correct, else the golden answer."""
    flagged = [o for o in spec.options if o.get("is_correct")]
    if len(flagged) == 1:
        return str(flagged[0]["option_key"]).strip()
    golden = spec.golden_answer.strip()
    return golden or None


class ChoiceEvaluator:
    """Deterministic grading of objective questions."""

    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation | None:
        if not spec.is_objective:
            return None
        correct = correct_option_key(spec)
        if not correct or not answer.strip():
            return None

        if spec.question_type == "true_false":
            verdict = self._true_false_verdict(spec, answer, correct)
        else:
            verdict = self._choice_verdict(spec, answer, correct)
        if verdict is None:
            return None

        chosen, is_correct = verdict
        return Evaluation(
            awarded_marks=spec.max_marks if is_correct else 0.0,
            confidence=1.0,
            reasoning=(
                f"The student marked {chosen}; the answer key is {correct}. "
                f"{'Correct' if is_correct else 'Incorrect'} (graded without a model)."
            ),
            evidence=[answer.strip()],
            snapshot={"evaluator": "deterministic", "question_type": spec.question_type},
        )

    @staticmethod
    def _choice_verdict(spec: QuestionSpec, answer: str, correct: str) -> tuple[str, bool] | None:
        keys = {str(o["option_key"]).strip().casefold(): str(o["option_key"]) for o in spec.options}
        match = _LEADING_TOKEN.match(answer.strip())
        token = match.group(1) if match else ""
        chosen: str | None = None
        if keys:
            chosen = keys.get(token.casefold())
            if chosen is None:  # the student wrote only the option's text
                texts = {
                    str(o["option_text"]).strip().casefold(): str(o["option_key"])
                    for o in spec.options
                }
                chosen = texts.get(answer.strip().casefold())
        elif len(token) == 1 and token.isalpha() and len(answer.strip()) <= 3:
            chosen = token.upper()
        if chosen is None:
            return None
        return chosen, chosen.casefold() == correct.casefold()

    @staticmethod
    def _true_false_verdict(
        spec: QuestionSpec, answer: str, correct: str
    ) -> tuple[str, bool] | None:
        by_key = {
            str(o["option_key"]).strip().casefold(): str(o["option_text"]) for o in spec.options
        }
        correct_text = by_key.get(correct.casefold(), correct)
        expected = _true_false(correct_text)
        chosen = _true_false(answer)
        if chosen is None:
            chosen = _true_false(by_key.get(answer.strip().casefold(), ""))
        if expected is None or chosen is None:
            return None
        return chosen.title(), chosen == expected


class LLMEvaluator:
    """Model-based grading."""

    def __init__(self, grader: LLMGrader | None = None) -> None:
        self.grader = grader or LLMGrader()

    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation:
        if spec.is_objective:
            return self._objective(spec, answer)
        return self._descriptive(spec, answer)

    def _objective(self, spec: QuestionSpec, answer: str) -> Evaluation:
        result, model = self.grader.grade_mcq(
            question_id=spec.question_id,
            student_answer=answer,
            golden_answer=correct_option_key(spec) or spec.golden_answer,
            question_text=spec.display_text(),
        )
        return Evaluation(
            awarded_marks=spec.max_marks if result["is_correct"] else 0.0,
            confidence=float(result["confidence"]),
            reasoning=result.get("reasoning", ""),
            llm_reasoning=result,
            model=model,
            snapshot={"evaluator": "llm", "model": model, "question_type": spec.question_type},
        )

    def _descriptive(self, spec: QuestionSpec, answer: str) -> Evaluation:
        result, model = self.grader.grade(
            question_id=spec.question_id,
            student_answer=answer,
            golden_answer=spec.golden_answer,
            rubric=spec.criteria,
            max_marks=spec.max_marks,
            guidance=spec.guidance,
            question_text=spec.question_text,
            question_type=spec.question_type,
        )
        return Evaluation(
            awarded_marks=max(0.0, min(float(result["awarded_marks"]), spec.max_marks)),
            confidence=float(result["confidence"]),
            reasoning=result.get("reasoning") or "",
            evidence=result.get("evidence") or [],
            criteria_scores=result.get("criteria_scores") or [],
            llm_reasoning=result,
            model=model,
            snapshot={"evaluator": "llm", "model": model, "question_type": spec.question_type},
        )


class EvaluationEngine:
    """Objective questions are graded deterministically when the answer is unambiguous and by
    the LLM otherwise; descriptive questions always go to the LLM."""

    def __init__(
        self,
        llm: Evaluator | None = None,
        choice: Evaluator | None = None,
    ) -> None:
        self.llm = llm or LLMEvaluator()
        self.choice = choice or ChoiceEvaluator()

    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation:
        if spec.is_objective:
            decided = self.choice.evaluate(spec, answer)
            if decided is not None:
                return decided
        evaluation = self.llm.evaluate(spec, answer)
        if evaluation is None:  # the LLM evaluator always decides; guards a bad injected one
            raise RuntimeError("The LLM evaluator returned no evaluation")
        return evaluation
