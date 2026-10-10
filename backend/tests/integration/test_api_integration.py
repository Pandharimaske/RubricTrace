"""
API integration tests for RubricTrace routes, run against a live server on localhost:8000.

They skip when no server is up. The in-process flow tests (test_exam_flow.py) cover the real
behaviour; these only smoke-test a running deployment.
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
