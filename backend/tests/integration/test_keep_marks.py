"""A flagged answer keeps the AI's mark until the teacher overrides it.

Flagging is a separate piece of state from the mark: it only decides what shows up in the
review queue. These tests pin that down, including the two ways a mark used to be lost: a
re-grade whose model call failed (now the stale mark is cleared up front and the answer is
retried on the next run), and a re-grade of an answer the teacher had already approved.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from backend.app.container import Container
from backend.app.main import create_app
from backend.app.models.domain import QuestionSpec
from fastapi.testclient import TestClient

from conftest import FakeEngine

SHORT = {
    "question_id": "Q2",
    "question_type": "short_answer",
    "question_text": "Define supervised learning.",
    "golden_answer": "Learning from labeled data.",
    "max_marks": 2,
    "criteria": [{"name": "Definition", "marks": 2, "expected_concepts": ["labeled data"]}],
}


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(confidence=0.5)  # below the default 0.65 threshold: every answer is flagged


@pytest.fixture
def container(make_container: Callable[..., Container], engine: FakeEngine) -> Container:
    return make_container(engine)


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as test_client:
        yield test_client


def _wait(client: TestClient, exam_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/exams/{exam_id}/job").json()["job"]
        if job and job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def graded_exam(client: TestClient) -> tuple[str, str]:
    """An exam with one short-answer question, one script, graded (and flagged)."""
    exam_id = client.post("/api/exams", json={"name": "Keep marks"}).json()["exam_id"]
    client.put(f"/api/exams/{exam_id}", json={"questions": [SHORT]})
    upload = client.post(
        "/api/scripts/upload",
        files={"file": ("a.txt", b"Q2. Learning from labeled data", "text/plain")},
        data={"exam_id": exam_id, "student_name": "Alice"},
    ).json()
    client.post(f"/api/exams/{exam_id}/process")
    _wait(client, exam_id)
    client.post(f"/api/exams/{exam_id}/grade")
    assert _wait(client, exam_id)["status"] == "done"
    return str(exam_id), str(upload["script_id"])


def test_a_flagged_answer_keeps_the_ai_mark_everywhere(
    client: TestClient, graded_exam: tuple[str, str]
) -> None:
    exam_id, script_id = graded_exam

    queue = client.get("/api/review-queue", params={"exam_id": exam_id}).json()
    results = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    detail = client.get(f"/api/scripts/{script_id}").json()

    assert queue["total"] == 1
    assert queue["items"][0]["awarded_marks"] == 2  # the AI's mark, not a placeholder 0
    assert results["marks"]["Q2"]["status"] == "needs_review"
    assert results["marks"]["Q2"]["awarded"] == 2
    assert results["total_awarded"] == 2
    assert detail["evaluations"][0]["awarded_marks"] == 2


def test_a_failed_regrade_clears_the_stale_mark_and_can_be_retried(
    client: TestClient, engine: FakeEngine, graded_exam: tuple[str, str]
) -> None:
    exam_id, _ = graded_exam
    engine.fail_for = {"Q2"}

    client.post(f"/api/exams/{exam_id}/grade", json={"regrade": True})
    job = _wait(client, exam_id)

    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"] == {}  # the old AI mark is gone and no placeholder 0 was stored
    assert client.get("/api/review-queue", params={"exam_id": exam_id}).json()["total"] == 0
    assert job["status"] == "done"
    assert "could not be graded" in job["error"]

    engine.fail_for = set()
    client.post(f"/api/exams/{exam_id}/grade")  # a plain "grade" picks the answer up again
    assert _wait(client, exam_id)["status"] == "done"
    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"]["Q2"]["awarded"] == 2


def test_regrade_replaces_flagged_answers_with_fresh_grades(
    client: TestClient, engine: FakeEngine, graded_exam: tuple[str, str]
) -> None:
    exam_id, _ = graded_exam
    assert client.get("/api/review-queue", params={"exam_id": exam_id}).json()["total"] == 1
    engine.confidence = 0.95

    client.post(f"/api/exams/{exam_id}/grade", json={"regrade": True})
    assert _wait(client, exam_id)["status"] == "done"

    assert client.get("/api/review-queue", params={"exam_id": exam_id}).json()["total"] == 0
    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"]["Q2"]["status"] == "scored"


def test_a_failed_first_grade_stores_nothing_and_is_retried_next_time(
    client: TestClient, engine: FakeEngine
) -> None:
    exam_id = client.post("/api/exams", json={"name": "Flaky"}).json()["exam_id"]
    client.put(f"/api/exams/{exam_id}", json={"questions": [SHORT]})
    client.post(
        "/api/scripts/upload",
        files={"file": ("a.txt", b"Q2. Learning from labeled data", "text/plain")},
        data={"exam_id": exam_id, "student_name": "Bob"},
    )
    client.post(f"/api/exams/{exam_id}/process")
    _wait(client, exam_id)
    engine.fail_for = {"Q2"}
    client.post(f"/api/exams/{exam_id}/grade")
    _wait(client, exam_id)

    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"] == {}  # no fake zero was recorded
    assert client.get("/api/review-queue", params={"exam_id": exam_id}).json()["total"] == 0

    engine.fail_for = set()
    client.post(f"/api/exams/{exam_id}/grade")  # a plain "grade" picks the ungraded answer up
    assert _wait(client, exam_id)["status"] == "done"
    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"]["Q2"]["awarded"] == 2


def test_a_teacher_approved_mark_survives_any_regrade_path(
    client: TestClient, container: Container, graded_exam: tuple[str, str]
) -> None:
    exam_id, script_id = graded_exam
    client.post(
        f"/api/scripts/{script_id}/override",
        json={"question_id": "Q2", "awarded_marks": 0.5, "reason": "too generous"},
    )

    # What the per-script endpoint does: grade the answer again, straight through the service.
    container.grading.grade_and_store(
        script_id, QuestionSpec.from_mapping(SHORT), "Learning from labeled data", 0.65
    )

    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"]["Q2"]["awarded"] == 0.5
    assert row["marks"]["Q2"]["status"] == "teacher_approved"
