"""
API integration tests for RubricTrace routes.
"""

import pytest
import requests

BASE_URL = "http://localhost:8000/api"
TIMEOUT = 10


def _is_server_up() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=1)
        return r.status_code == 200
    except requests.RequestException:
        return False


server_available = pytest.mark.skipif(
    not _is_server_up(), reason="RubricTrace API server is not running at localhost:8000"
)


@server_available
def test_health():
    res = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"


@server_available
def test_stats():
    res = requests.get(f"{BASE_URL}/stats", timeout=TIMEOUT)
    assert res.status_code == 200
    data = res.json()
    assert "total_students" in data
    assert "total_scripts" in data
    assert "needs_review" in data


@server_available
def test_review_queue():
    res = requests.get(f"{BASE_URL}/review-queue", timeout=TIMEOUT)
    assert res.status_code == 200
    data = res.json()
    assert "total" in data
    assert isinstance(data["items"], list)


@server_available
def test_rubric_configs_crud():
    # Create
    cfg_payload = {
        "name": "Integration Test Rubric",
        "config": {
            "questions": [
                {
                    "question_id": "Q1",
                    "golden_answer": "Test answer",
                    "max_marks": 5,
                    "criteria": [
                        {
                            "name": "Accuracy",
                            "marks": 5,
                            "expected_concepts": ["test"],
                            "guidance": "",
                        }
                    ],
                }
            ]
        },
    }
    create_res = requests.post(f"{BASE_URL}/rubric-configs", json=cfg_payload, timeout=TIMEOUT)
    assert create_res.status_code == 200
    config_id = create_res.json()["config_id"]

    # Read
    get_res = requests.get(f"{BASE_URL}/rubric-configs/{config_id}", timeout=TIMEOUT)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["name"] == "Integration Test Rubric"
    assert len(data["config"]["questions"]) == 1
