"""Shared fixtures: a Container wired to a temporary data directory and a scripted fake grader."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from backend.app.container import Container
from backend.app.core.config import Settings
from backend.app.db.database import Database
from backend.app.grading.evaluators import Evaluation, EvaluationEngine
from backend.app.models.domain import QuestionSpec


class FakeEngine(EvaluationEngine):
    """Awards full marks at a fixed confidence, with no model call. Deterministic choice
    grading still runs first for objective questions (that is the real routing logic)."""

    def __init__(self, confidence: float = 0.9) -> None:
        super().__init__(llm=self)
        self.confidence = confidence
        self.calls: list[str] = []
        self.fail_for: set[str] = set()

    def evaluate(self, spec: QuestionSpec, answer: str) -> Evaluation:
        if spec.question_id in self.fail_for:
            raise RuntimeError("simulated grading failure")
        if spec.is_objective:
            decided = self.choice.evaluate(spec, answer)
            if decided is not None:
                return decided
        self.calls.append(spec.question_id)
        return Evaluation(
            awarded_marks=spec.max_marks,
            confidence=self.confidence,
            reasoning="Covers the golden answer.",
            evidence=[answer[:80]],
            llm_reasoning={"reasoning": "fake"},
            model="fake-llm",
            snapshot={"evaluator": "llm", "model": "fake-llm"},
        )


@pytest.fixture
def config(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path, _env_file=None)


@pytest.fixture
def database(config: Settings) -> Iterator[Database]:
    """SQLite in the temp dir by default. Set RUBRICTRACE_TEST_DATABASE_URL to a PostgreSQL URL
    to run the same tests against PostgreSQL, each in its own throwaway schema."""
    url = os.environ.get("RUBRICTRACE_TEST_DATABASE_URL", "")
    if not url:
        yield Database(sqlite_path=config.db_path)
        return

    import psycopg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    separator = "&" if "?" in url else "?"
    yield Database(url=f"{url}{separator}options=-csearch_path%3D{schema}")
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def make_container(config: Settings, database: Database) -> Callable[..., Container]:
    def _make(engine: EvaluationEngine | None = None) -> Container:
        container = Container(
            config=config,
            db=database,
            engine=engine or FakeEngine(),
        )
        container.startup()
        return container

    return _make
