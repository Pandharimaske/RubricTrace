from __future__ import annotations

import pytest
from backend.app.models.domain import (
    QuestionSpec,
    ReviewPolicy,
    normalize_question_type,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MCQ", "mcq"),
        ("Multiple Choice", "mcq"),
        ("true/false", "true_false"),
        ("True-False", "true_false"),
        ("Short answer", "short_answer"),
        ("long_answer", "long_answer"),
        ("", ""),
        (None, ""),
    ],
)
def test_question_type_spellings_are_normalized(raw: str | None, expected: str) -> None:
    assert normalize_question_type(raw) == expected


def test_review_policy_flags_only_below_threshold() -> None:
    policy = ReviewPolicy(0.8)

    assert policy.verdict(0.79)[0] is True
    assert policy.verdict(0.8)[0] is False
    assert policy.effective_status("scored", 0.5) == "needs_review"
    assert policy.effective_status("needs_review", 0.95) == "scored"


def test_teacher_approved_answers_are_never_flagged_again() -> None:
    assert ReviewPolicy(0.99).effective_status("teacher_approved", 0.1) == "teacher_approved"


def test_review_policy_clamps_out_of_range_thresholds() -> None:
    assert ReviewPolicy.clamp(5) == 1.0
    assert ReviewPolicy.clamp(0) == ReviewPolicy.MIN_THRESHOLD
    assert ReviewPolicy.clamp("nonsense") == ReviewPolicy.DEFAULT_THRESHOLD


def test_stale_confidence_reasons_are_rebuilt_from_the_current_threshold() -> None:
    policy = ReviewPolicy(0.5)
    stale = ["Low confidence (0.40) is below the review threshold (0.65).", "Answer looks blank"]

    reasons = policy.flag_reasons(stale, confidence=0.7, status="scored")

    assert reasons == ["Answer looks blank"]


def test_question_spec_appends_options_missing_from_the_question_text() -> None:
    spec = QuestionSpec.from_mapping(
        {
            "question_id": "Q1",
            "question_text": "Pick one",
            "question_type": "MCQ",
            "max_marks": 1,
            "options": [
                {"option_key": "A", "option_text": "red"},
                {"option_key": "B", "option_text": "blue"},
            ],
        }
    )

    assert spec.question_type == "mcq"
    assert spec.display_text() == "Pick one\nA. red\nB. blue"


def test_question_spec_does_not_duplicate_options_already_in_the_text() -> None:
    spec = QuestionSpec.from_mapping(
        {
            "question_id": "Q1",
            "question_text": "Pick one\nA. red\nB. blue",
            "question_type": "mcq",
            "max_marks": 1,
            "options": [{"option_key": "A", "option_text": "red"}],
        }
    )

    assert spec.display_text() == "Pick one\nA. red\nB. blue"
