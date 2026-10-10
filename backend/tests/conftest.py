"""Shared fixtures: a Container wired to a temporary data directory and a scripted fake grader."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
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
def database() -> Iterator[Database]:
    """A PostgreSQL schema created for this test, with the real migrations applied, dropped
    afterwards. Set RUBRICTRACE_TEST_DATABASE_URL (a local container or a scratch Supabase
    project); tests that need a database are skipped without it."""
    url = os.environ.get("RUBRICTRACE_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("set RUBRICTRACE_TEST_DATABASE_URL to run the database tests")

    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"create schema {schema}")
    db = Database(url, schema=schema, max_connections=4)
    try:
        db.migrate()
        yield db
    finally:
        db.close()
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f"drop schema {schema} cascade")


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
