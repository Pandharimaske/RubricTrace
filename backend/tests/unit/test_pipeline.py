from __future__ import annotations

import json
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.extraction.segment import segment_questions
from backend.app.grading.batch import BatchGrader
from backend.app.grading.evaluators import Evaluation
from backend.app.grading.grader import LLMGrader
from backend.app.grading.service import AnswerGrader
from backend.app.llm.errors import ModelUnavailable
from backend.app.models.domain import QuestionSpec

from conftest import FakeEngine

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_llm_grader_resolves_the_configured_model() -> None:
    grader = LLMGrader(config=Settings(nvidia_llm_model="configured-llm", _env_file=None))

    _client, model = grader.resolve()

    assert model == "configured-llm"


def test_default_grading_model_is_gpt_oss_20b() -> None:
    _client, model = LLMGrader(config=Settings(_env_file=None)).resolve()

    assert model == "openai/gpt-oss-20b"


def test_injected_client_without_a_model_uses_the_configured_grading_model() -> None:
    config = Settings(nvidia_llm_model="meta/other-model", _env_file=None)

    _client, model = LLMGrader(client=object(), config=config).resolve()  # type: ignore[arg-type]

    assert model == "meta/other-model"


def test_segment_questions_normalizes_question_ids() -> None:
    questions = segment_questions("Q1. First answer\n\nQuestion 2: Second answer")

    assert questions == {"1": "First answer", "2": "Second answer"}


def test_demo_batch_scores_records(tmp_path: Path) -> None:
    output_path = tmp_path / "results.json"

    payload = BatchGrader(AnswerGrader(FakeEngine(confidence=0.92))).run(
        BACKEND_ROOT / "data/samples/demo_metadata.csv", output_path
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
    assert payload["results"][0]["llm_model"] == "fake-llm"


def test_blank_answer_scores_zero_without_a_model_call_and_is_not_flagged(
    tmp_path: Path,
) -> None:
    metadata_path = tmp_path / "metadata.csv"
    metadata_path.write_text(
        "student_id,script_path,question_id,max_marks,manual_marks,criteria_json\n"
        'student,answer.txt,Q1,2,0,"[{""name"": ""coverage"", ""marks"": 2, '
        '""expected_concepts"": [""expected""]}]"\n',
        encoding="utf-8",
    )
    (tmp_path / "answer.txt").write_text("", encoding="utf-8")
    engine = FakeEngine()

    payload = BatchGrader(AnswerGrader(engine)).run(metadata_path, tmp_path / "results.json")

    result = payload["results"][0]
    assert engine.calls == []
    assert payload["summary"]["needs_review"] == 0
    assert payload["summary"]["scored"] == 1
    assert result["status"] == "scored"
    assert result["awarded_marks"] == 0
    assert result["confidence"] == 1.0
    assert result["llm_model"] is None


def test_whitespace_only_answer_is_treated_as_blank() -> None:
    spec = QuestionSpec.from_mapping({"question_id": "Q1", "max_marks": 2, "golden_answer": "x"})
    engine = FakeEngine()

    result = AnswerGrader(engine).grade(spec, "  \n\t ", threshold=0.99)

    assert engine.calls == []
    assert result.awarded_marks == 0
    assert result.needs_review is False
    assert result.status == "scored"


def test_batch_resumes_without_regrading_finished_records(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.csv"
    metadata_path.write_text(
        "student_id,script_path,question_id,max_marks,answer_text\n"
        "s1,unused.txt,Q1,2,First\n"
        "s2,unused2.txt,Q1,2,Second\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "results.json"
    first = FakeEngine()
    BatchGrader(AnswerGrader(first)).run(metadata_path, output_path, student_ids=["s1"])

    second = FakeEngine()
    payload = BatchGrader(AnswerGrader(second)).run(metadata_path, output_path)

    assert first.calls == ["Q1"]
    assert second.calls == ["Q1"]  # only s2 was graded the second time
    assert [r["student_id"] for r in payload["results"]] == ["s1", "s2"]


class _FlakyEngine(FakeEngine):
    """Fails like an unreachable model until `down` is switched off."""

    down = True

    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation:
        if self.down:
            raise ModelUnavailable("model unreachable")
        return super().evaluate(spec, answer)


def test_batch_retries_records_that_failed_because_the_model_was_down(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.csv"
    metadata_path.write_text(
        "student_id,script_path,question_id,max_marks,answer_text\ns1,unused.txt,Q1,2,An answer\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "results.json"
    engine = _FlakyEngine()
    grader = BatchGrader(AnswerGrader(engine))

    first = grader.run(metadata_path, output_path)
    engine.down = False
    second = grader.run(metadata_path, output_path)

    assert first["results"][0]["awarded_marks"] == 0
    assert first["results"][0]["flag_reasons"][0].startswith("LLM unavailable")
    assert len(second["results"]) == 1
    assert second["results"][0]["awarded_marks"] == 2
    assert second["summary"]["needs_review"] == 0


def test_low_confidence_answers_are_flagged_with_a_reason() -> None:
    spec = QuestionSpec.from_mapping({"question_id": "Q1", "max_marks": 2, "golden_answer": "x"})

    result = AnswerGrader(FakeEngine(confidence=0.4)).grade(spec, "an answer", threshold=0.65)

    assert result.status == "needs_review"
    assert result.flag_reasons
    assert result.awarded_marks == 2
