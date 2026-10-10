"""End-to-end API flow against a temporary database, with a fake grading model.

Unlike test_api_integration.py (which needs a server running on localhost:8000), these run
in-process, so they are part of every normal `pytest` run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from backend.app.container import Container
from backend.app.main import create_app
from fastapi.testclient import TestClient

from conftest import FakeEngine

MCQ = {
    "question_id": "Q1",
    "question_type": "mcq",
    "question_text": "Which is right?",
    "golden_answer": "B",
    "max_marks": 1,
    "options": [
        {"option_key": "A", "option_text": "wrong", "is_correct": False},
        {"option_key": "B", "option_text": "right", "is_correct": True},
    ],
}
SHORT = {
    "question_id": "Q2",
    "question_type": "short_answer",
    "question_text": "Define supervised learning.",
    "golden_answer": "Learning from labeled data.",
    "max_marks": 2,
    "criteria": [{"name": "Definition", "marks": 2, "expected_concepts": ["labeled data"]}],
}


def _wait_for_job(client: TestClient, exam_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/api/exams/{exam_id}/job").json()["job"]
        if job and job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(confidence=0.5)


@pytest.fixture
def client(make_container: Callable[..., Container], engine: FakeEngine) -> Any:
    with TestClient(create_app(make_container(engine))) as test_client:
        yield test_client


@pytest.fixture
def exam_id(client: TestClient) -> str:
    created = client.post("/api/exams", json={"name": "Midterm"}).json()
    updated = client.put(f"/api/exams/{created['exam_id']}", json={"questions": [MCQ, SHORT]})
    assert updated.status_code == 200, updated.text
    return str(created["exam_id"])


def _upload(client: TestClient, exam_id: str, name: str = "Alice") -> dict[str, Any]:
    response = client.post(
        "/api/scripts/upload",
        files={"file": (f"{name}.txt", b"Q1. B\n\nQ2. Uses labeled data", "text/plain")},
        data={"exam_id": exam_id, "student_name": name},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_process_and_grade_an_exam(client: TestClient, exam_id: str, engine: FakeEngine) -> None:
    _upload(client, exam_id)

    assert client.post(f"/api/exams/{exam_id}/process").status_code == 200
    assert _wait_for_job(client, exam_id)["status"] == "done"
    assert client.post(f"/api/exams/{exam_id}/grade").status_code == 200
    assert _wait_for_job(client, exam_id)["status"] == "done"

    results = client.get(f"/api/exams/{exam_id}/results").json()
    row = results["rows"][0]
    assert row["total_awarded"] == 3
    assert row["marks"]["Q1"]["confidence"] == 1.0  # graded without a model call
    assert engine.calls == ["Q2"]  # the multiple-choice question never reached the model
    assert row["needs_review_count"] == 1  # Q2: confidence 0.5 is under the default 0.65


def test_changing_the_review_threshold_applies_to_existing_grades(
    client: TestClient, exam_id: str
) -> None:
    _upload(client, exam_id)
    client.post(f"/api/exams/{exam_id}/process")
    _wait_for_job(client, exam_id)
    client.post(f"/api/exams/{exam_id}/grade")
    _wait_for_job(client, exam_id)
    assert client.get("/api/review-queue", params={"exam_id": exam_id}).json()["total"] == 1

    client.put(f"/api/exams/{exam_id}", json={"review_confidence_threshold": 0.4})

    queue = client.get("/api/review-queue", params={"exam_id": exam_id}).json()
    assert queue["total"] == 0
    results = client.get(f"/api/exams/{exam_id}/results").json()
    assert results["rows"][0]["needs_review_count"] == 0


def test_teacher_override_removes_an_answer_from_the_review_queue(
    client: TestClient, exam_id: str
) -> None:
    script = _upload(client, exam_id)
    client.post(f"/api/exams/{exam_id}/process")
    _wait_for_job(client, exam_id)
    client.post(f"/api/exams/{exam_id}/grade")
    _wait_for_job(client, exam_id)

    response = client.post(
        f"/api/scripts/{script['script_id']}/override",
        json={"question_id": "Q2", "awarded_marks": 1.5, "reason": "partly right"},
    )

    assert response.status_code == 200, response.text
    assert client.get("/api/review-queue", params={"exam_id": exam_id}).json()["total"] == 0
    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["total_awarded"] == 2.5
    assert row["status"] == "graded"


def test_regrading_never_overwrites_a_teacher_approved_mark(
    client: TestClient, exam_id: str
) -> None:
    script = _upload(client, exam_id)
    client.post(f"/api/exams/{exam_id}/process")
    _wait_for_job(client, exam_id)
    client.post(f"/api/exams/{exam_id}/grade")
    _wait_for_job(client, exam_id)
    client.post(
        f"/api/scripts/{script['script_id']}/override",
        json={"question_id": "Q2", "awarded_marks": 0.5, "reason": "too generous"},
    )

    client.post(f"/api/exams/{exam_id}/grade", json={"regrade": True})
    _wait_for_job(client, exam_id)

    row = client.get(f"/api/exams/{exam_id}/results").json()["rows"][0]
    assert row["marks"]["Q2"]["awarded"] == 0.5


def test_unsupported_and_empty_uploads_are_rejected(client: TestClient) -> None:
    bad_type = client.post(
        "/api/scripts/upload", files={"file": ("virus.exe", b"MZ", "application/octet-stream")}
    )
    empty = client.post("/api/scripts/upload", files={"file": ("a.pdf", b"", "application/pdf")})

    assert bad_type.status_code == 400
    assert empty.status_code == 400


def test_grading_requires_a_reference_answer_for_every_question(client: TestClient) -> None:
    created = client.post("/api/exams", json={"name": "Draft"}).json()
    unfinished = {**SHORT, "golden_answer": ""}
    client.put(f"/api/exams/{created['exam_id']}", json={"questions": [unfinished]})
    _upload(client, created["exam_id"])
    client.post(f"/api/exams/{created['exam_id']}/process")
    _wait_for_job(client, created["exam_id"])

    response = client.post(f"/api/exams/{created['exam_id']}/grade")

    assert response.status_code == 400
    assert "Q2" in response.json()["detail"]


def test_deleting_an_exam_removes_its_scripts_and_files(
    client: TestClient, exam_id: str, config: Any
) -> None:
    script = _upload(client, exam_id)

    assert client.delete(f"/api/exams/{exam_id}").status_code == 200

    assert client.get(f"/api/scripts/{script['script_id']}").status_code == 404
    assert list(config.upload_dir.glob(f"{script['script_id']}.*")) == []


def test_health_and_stats(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "healthy"
    assert {"total_students", "needs_review", "approved"} <= set(client.get("/api/stats").json())


def test_confidence_equal_to_the_threshold_is_not_flagged(
    make_container: Callable[..., Container],
) -> None:
    """Float storage must not turn 0.9 vs 0.9 into 0.8999... < 0.9 (PostgreSQL REAL is 32-bit)."""
    with TestClient(create_app(make_container(FakeEngine(confidence=0.9)))) as client:
        exam = client.post("/api/exams", json={"name": "Edge"}).json()["exam_id"]
        client.put(
            f"/api/exams/{exam}",
            json={"questions": [SHORT], "review_confidence_threshold": 0.9},
        )
        _upload(client, exam)
        client.post(f"/api/exams/{exam}/process")
        _wait_for_job(client, exam)
        client.post(f"/api/exams/{exam}/grade")
        _wait_for_job(client, exam)

        queue = client.get("/api/review-queue", params={"exam_id": exam}).json()
        row = client.get(f"/api/exams/{exam}/results").json()["rows"][0]
        script = client.get(f"/api/scripts/{row['script_id']}").json()

    assert queue["total"] == 0
    assert row["needs_review_count"] == 0
    assert [e["status"] for e in script["evaluations"]] == ["scored"]
