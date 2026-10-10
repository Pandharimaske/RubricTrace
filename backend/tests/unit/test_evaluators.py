from __future__ import annotations

from typing import Any

import pytest
from backend.app.grading.evaluators import ChoiceEvaluator, Evaluation, EvaluationEngine
from backend.app.models.domain import QuestionSpec

OPTIONS = [
    {"option_key": "A", "option_text": "Little or no autocorrelation", "is_correct": False},
    {"option_key": "B", "option_text": "Strong autocorrelation", "is_correct": True},
    {"option_key": "C", "option_text": "Heteroscedasticity", "is_correct": False},
]


def _mcq(**extra: Any) -> QuestionSpec:
    return QuestionSpec.from_mapping(
        {
            "question_id": "Q1",
            "question_type": "mcq",
            "max_marks": 1,
            "options": OPTIONS,
            **extra,
        }
    )


def _tf(golden: str = "True") -> QuestionSpec:
    return QuestionSpec.from_mapping(
        {
            "question_id": "Q9",
            "question_type": "true_false",
            "max_marks": 1,
            "golden_answer": golden,
        }
    )


@pytest.mark.parametrize("answer", ["B", "b", "B.", "(B)", "B) Strong autocorrelation"])
def test_correct_option_in_any_common_format_earns_full_marks(answer: str) -> None:
    result = ChoiceEvaluator().evaluate(_mcq(), answer)

    assert result is not None
    assert result.awarded_marks == 1
    assert result.confidence == 1.0


def test_wrong_option_earns_zero_with_full_confidence() -> None:
    result = ChoiceEvaluator().evaluate(_mcq(), "C")

    assert result is not None
    assert result.awarded_marks == 0
    assert result.confidence == 1.0


def test_option_written_out_in_words_is_matched_to_its_key() -> None:
    result = ChoiceEvaluator().evaluate(_mcq(), "Strong autocorrelation")

    assert result is not None
    assert result.awarded_marks == 1


@pytest.mark.parametrize("answer", ["", "A, B", "Answer: maybe", "not sure"])
def test_ambiguous_or_missing_choices_are_left_to_the_model(answer: str) -> None:
    assert ChoiceEvaluator().evaluate(_mcq(), answer) is None


def test_golden_answer_letter_works_without_flagged_options() -> None:
    spec = QuestionSpec.from_mapping(
        {"question_id": "Q1", "question_type": "mcq", "max_marks": 2, "golden_answer": "c"}
    )

    right = ChoiceEvaluator().evaluate(spec, "C")
    wrong = ChoiceEvaluator().evaluate(spec, "A")

    assert right is not None
    assert right.awarded_marks == 2
    assert wrong is not None
    assert wrong.awarded_marks == 0


@pytest.mark.parametrize(
    ("answer", "expected"), [("True", 1), ("t", 1), ("TRUE.", 1), ("False", 0), ("f", 0)]
)
def test_true_false_answers(answer: str, expected: float) -> None:
    result = ChoiceEvaluator().evaluate(_tf("True"), answer)

    assert result is not None
    assert result.awarded_marks == expected


def test_descriptive_questions_are_never_graded_deterministically() -> None:
    spec = QuestionSpec.from_mapping(
        {"question_id": "Q3", "question_type": "short_answer", "max_marks": 2, "golden_answer": "x"}
    )

    assert ChoiceEvaluator().evaluate(spec, "x") is None


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation:
        self.calls += 1
        return Evaluation(awarded_marks=0.5, confidence=0.7, model="llm")


def test_engine_skips_the_model_when_the_choice_is_clear() -> None:
    llm = _RecordingLLM()

    result = EvaluationEngine(llm=llm).evaluate(_mcq(), "B")

    assert result.awarded_marks == 1
    assert llm.calls == 0


def test_engine_falls_back_to_the_model_when_the_choice_is_unclear() -> None:
    llm = _RecordingLLM()

    result = EvaluationEngine(llm=llm).evaluate(_mcq(), "I think it is the second one")

    assert result.model == "llm"
    assert llm.calls == 1
