import json
from pathlib import Path
from unittest.mock import patch

import pymupdf
from backend.app.extraction.segment import segment_questions
from backend.app.grading.pipeline import evaluate_teacher_config, run_pipeline
from backend.app.models.schemas import (
    GradingCriterion,
    QuestionGradingConfig,
    TeacherEvaluationRequest,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_segment_questions_normalizes_question_ids() -> None:
    questions = segment_questions("Q1. First answer\n\nQuestion 2: Second answer")

    assert questions == {"1": "First answer", "2": "Second answer"}


def _llm_full_marks(**kwargs):
    max_marks = kwargs["max_marks"]
    answer = kwargs["student_answer"]
    return (
        {
            "awarded_marks": max_marks,
            "reasoning": (
                f"The student answer covers the golden answer and rubric. "
                f"Awarded {max_marks}/{max_marks}."
            ),
            "evidence": [answer[:120]],
            "concepts_found": ["covered"],
            "concepts_missing": [],
            "criteria_satisfied": [c.get("name") for c in kwargs.get("rubric", [])],
            "criteria_partial": [],
            "criteria_failed": [],
            "confidence": 0.92,
            "needs_review": False,
            "review_reason": "",
        },
        "mock-llm",
    )


@patch("backend.app.grading.pipeline.llm_grade", side_effect=_llm_full_marks)
def test_demo_pipeline_scores_records(_mock_llm, tmp_path: Path) -> None:
    output_path = tmp_path / "results.json"
    payload = run_pipeline(
        BACKEND_ROOT / "data/samples/demo_metadata.csv",
        output_path,
    )

    assert payload["summary"] == {
        "records": 2,
        "scored": 2,
        "needs_ocr": 0,
        "needs_review": 0,
        "manual_mark_records": 2,
        "mae": 0.0,
    }
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
    assert payload["results"][0]["llm_model"] == "mock-llm"
    assert (
        "golden answer" in payload["results"][0]["reasoning"].lower()
        or "Awarded" in payload["results"][0]["reasoning"]
    )


def test_empty_extracted_answer_needs_review(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.csv"
    metadata_path.write_text(
        "student_id,script_path,question_id,max_marks,manual_marks,criteria_json\n"
        'student,answer.txt,Q1,2,1,"[{""name"": ""coverage"", ""marks"": 2, '
        '""expected_concepts"": [""expected""]}]"\n',
        encoding="utf-8",
    )
    (tmp_path / "answer.txt").write_text("", encoding="utf-8")

    payload = run_pipeline(metadata_path, tmp_path / "results.json")

    assert payload["summary"]["needs_review"] == 1
    assert payload["results"][0]["status"] == "needs_review"
    assert payload["results"][0]["llm_model"] is None


@patch("backend.app.grading.pipeline.extract_script_questions")
@patch("backend.app.grading.pipeline.llm_grade", side_effect=_llm_full_marks)
def test_teacher_evaluation_saves_question_results(mock_llm, mock_extract, tmp_path: Path) -> None:
    script_path = tmp_path / "student.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Q1. Supervised learning uses labeled data.")
    document.save(script_path)
    document.close()

    mock_extract.return_value = {
        "questions": {"Q1": "Supervised learning uses labeled data."},
        "pages": [],
        "crops": {},  # Still empty, but no longer used
        "method": "ollama_vlm",
        "vlm_model": "mock-vlm",
        "page_count": 1,
    }

    request = TeacherEvaluationRequest(
        script_id="student-1",
        questions=[
            QuestionGradingConfig(
                question_id="Q1",
                golden_answer="Supervised learning uses labeled data.",
                max_marks=2,
                criteria=[
                    GradingCriterion(
                        name="definition",
                        marks=2,
                        expected_concepts=["supervised learning", "labeled data"],
                    )
                ],
            )
        ],
    )

    evaluation_path = BACKEND_ROOT / "data/processed/evaluations/student-1.json"
    try:
        payload = evaluate_teacher_config(request, script_path, "student-1")

        assert payload["total_max_marks"] == 2.0
        assert payload["results"][0]["question_id"] == "Q1"
        assert payload["results"][0]["awarded_marks"] == 2.0
        assert payload["results"][0]["llm_model"] == "mock-llm"
        assert mock_llm.called
        assert evaluation_path.exists()
    finally:
        evaluation_path.unlink(missing_ok=True)
