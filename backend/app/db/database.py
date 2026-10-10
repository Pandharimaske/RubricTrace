"""Database access for RubricTrace: a connection factory plus schema creation and migrations.

SQLite is the default; set RUBRICTRACE_DATABASE_URL to a postgres:// URL to use PostgreSQL.
The schema and migrations run once per process (the first time a connection is requested), not
on every connection. Queries live in the repository classes in db/repositories.py,
db/exams.py and db/jobs.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.core.config import Settings, settings


class _PgRow(dict):
    """A dict row that also supports positional access, like sqlite3.Row, so existing
    ``fetchone()[0]`` and ``row["name"]`` code both work on PostgreSQL."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _pg_row_factory(cursor: Any) -> Any:
    if cursor.description is None:
        return lambda values: values
    names = [col.name for col in cursor.description]
    return lambda values: _PgRow(zip(names, values, strict=True))


class PostgresConnection:
    """Small DB-API compatibility layer so repositories can write SQLite-flavoured SQL."""

    def __init__(self, url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL requires the psycopg[binary] dependency") from exc
        # prepare_threshold=None disables server-side auto-prepared statements, which collide
        # (DuplicatePreparedStatement) behind transaction-mode poolers such as PgBouncer/Supabase.
        self._connection = psycopg.connect(url, row_factory=_pg_row_factory, prepare_threshold=None)

    @staticmethod
    def _adapt_sql(sql: str) -> str:
        adapted = sql.replace("datetime('now')", "CURRENT_TIMESTAMP")
        adapted = adapted.replace(" COLLATE NOCASE", "")
        if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", adapted, re.IGNORECASE):
            adapted = re.sub(
                r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", adapted, flags=re.IGNORECASE
            )
            adapted = f"{adapted.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"
        return adapted.replace("?", "%s")

    def execute(self, sql: str, parameters: tuple | list = ()):  # type: ignore[no-untyped-def]
        return self._connection.execute(self._adapt_sql(sql), parameters)

    def executemany(self, sql: str, parameters):  # type: ignore[no-untyped-def]
        # psycopg connections have no executemany(); only cursors do.
        cursor = self._connection.cursor()
        cursor.executemany(self._adapt_sql(sql), parameters)
        return cursor

    def executescript(self, sql: str) -> None:
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


Connection = sqlite3.Connection | PostgresConnection


_DDL = """
CREATE TABLE IF NOT EXISTS students (
    student_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- An exam owns ONE answer key / rubric that applies to every student's script.
CREATE TABLE IF NOT EXISTS exams (
    exam_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{"questions": []}',
    -- Answers with confidence below this are shown as needing review.
    review_confidence_threshold REAL NOT NULL DEFAULT 0.65,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scripts (
    script_id    TEXT PRIMARY KEY,
    student_id   TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    exam_id      TEXT REFERENCES exams(exam_id) ON DELETE SET NULL,
    filename     TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'uploaded',
    page_count   INTEGER,
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS question_extractions (
    extraction_id     TEXT PRIMARY KEY,
    script_id         TEXT NOT NULL REFERENCES scripts(script_id) ON DELETE CASCADE,
    question_id       TEXT NOT NULL,
    question_number   INTEGER,
    question_type     TEXT NOT NULL DEFAULT 'unknown',
    source_path       TEXT,
    page_number       INTEGER,
    extracted_text    TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'unknown',
    ocr_confidence    REAL,
    vlm_confidence    REAL,
    extraction_confidence REAL,
    needs_review      INTEGER NOT NULL DEFAULT 0,
    review_reason     TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(script_id, question_id)
);

CREATE TABLE IF NOT EXISTS question_evaluations (
    eval_id                 TEXT PRIMARY KEY,
    script_id               TEXT NOT NULL REFERENCES scripts(script_id) ON DELETE CASCADE,
    question_id             TEXT NOT NULL,
    question_text           TEXT NOT NULL DEFAULT '',
    question_type           TEXT NOT NULL DEFAULT 'unknown',
    answer_text             TEXT NOT NULL DEFAULT '',
    page_number              INTEGER,
    golden_answer           TEXT NOT NULL DEFAULT '',
    rubric_json             TEXT NOT NULL DEFAULT '[]',
    max_marks               REAL NOT NULL,
    awarded_marks           REAL NOT NULL,
    confidence              REAL NOT NULL,
    threshold_used          REAL NOT NULL DEFAULT 0.65,
    criteria_scores_json    TEXT NOT NULL DEFAULT '[]',
    needs_review            INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'pending',
    evidence_json           TEXT NOT NULL DEFAULT '[]',
    reasoning               TEXT NOT NULL DEFAULT '',
    llm_reasoning_json      TEXT,
    llm_model               TEXT,
    evaluator_snapshot_json  TEXT,
    teacher_override        REAL,
    teacher_override_reason TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(script_id, question_id)
);

CREATE TABLE IF NOT EXISTS rubric_configs (
    config_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evaluator_configs (
    evaluator_config_id TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    evaluator_kind      TEXT NOT NULL CHECK (evaluator_kind IN ('deterministic', 'slm', 'llm')),
    provider_name       TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    temperature         REAL NOT NULL DEFAULT 0.0,
    max_tokens          INTEGER,
    fallback_config_id  TEXT REFERENCES evaluator_configs(evaluator_config_id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exam_questions (
    question_row_id             TEXT PRIMARY KEY,
    exam_id                     TEXT NOT NULL REFERENCES exams(exam_id) ON DELETE CASCADE,
    question_id                 TEXT NOT NULL,
    question_number             INTEGER NOT NULL,
    question_text               TEXT NOT NULL DEFAULT '',
    question_type               TEXT NOT NULL CHECK (
        question_type IN ('mcq', 'true_false', 'short_answer', 'long_answer')
    ),
    golden_answer               TEXT NOT NULL DEFAULT '',
    max_marks                   REAL NOT NULL CHECK (max_marks > 0),
    review_confidence_threshold REAL,
    evaluator_config_id         TEXT REFERENCES evaluator_configs(evaluator_config_id),
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(exam_id, question_number),
    UNIQUE(exam_id, question_id)
);

CREATE TABLE IF NOT EXISTS question_options (
    option_id     TEXT PRIMARY KEY,
    question_row_id TEXT NOT NULL REFERENCES exam_questions(question_row_id) ON DELETE CASCADE,
    option_key    TEXT NOT NULL,
    option_text   TEXT NOT NULL,
    is_correct    INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(question_row_id, option_key)
);

CREATE TABLE IF NOT EXISTS rubric_criteria (
    criterion_id    TEXT PRIMARY KEY,
    question_row_id TEXT NOT NULL REFERENCES exam_questions(question_row_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    max_marks       REAL NOT NULL CHECK (max_marks > 0),
    expected_concepts TEXT NOT NULL DEFAULT '[]',
    guidance        TEXT NOT NULL DEFAULT '',
    display_order   INTEGER NOT NULL DEFAULT 0
);

-- Background work (segmenting / grading a whole exam) with progress.
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    exam_id          TEXT NOT NULL REFERENCES exams(exam_id) ON DELETE CASCADE,
    kind             TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'queued',
    total            INTEGER NOT NULL DEFAULT 0,
    done             INTEGER NOT NULL DEFAULT 0,
    message          TEXT NOT NULL DEFAULT '',
    error            TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Columns older databases don't have yet: (table, column, definition).
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("scripts", "exam_id", "TEXT REFERENCES exams(exam_id)"),
    ("scripts", "error", "TEXT"),
    ("question_evaluations", "question_text", "TEXT NOT NULL DEFAULT ''"),
    ("question_evaluations", "page_number", "INTEGER"),
    ("question_evaluations", "question_type", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("question_evaluations", "answer_text", "TEXT NOT NULL DEFAULT ''"),
    ("question_evaluations", "threshold_used", "REAL NOT NULL DEFAULT 0.65"),
    ("question_evaluations", "criteria_scores_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("question_evaluations", "evaluator_snapshot_json", "TEXT"),
    ("exams", "review_confidence_threshold", "REAL NOT NULL DEFAULT 0.65"),
)

# Columns question_extractions gained after the old question_crops table was retired.
_EXTRACTION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("question_number", "INTEGER"),
    ("question_type", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("page_number", "INTEGER"),
    ("vlm_confidence", "REAL"),
    ("extraction_confidence", "REAL"),
    ("needs_review", "INTEGER NOT NULL DEFAULT 0"),
    ("review_reason", "TEXT NOT NULL DEFAULT ''"),
)


class Database:
    """Opens connections and makes sure the schema exists exactly once."""

    def __init__(self, url: str = "", sqlite_path: Path | None = None) -> None:
        self.url = url
        self.sqlite_path = sqlite_path
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    @classmethod
    def from_settings(cls, config: Settings) -> Database:
        return cls(url=config.database_url, sqlite_path=config.db_path)

    @property
    def is_postgres(self) -> bool:
        return self.url.startswith(("postgres://", "postgresql://"))

    def _open(self) -> Connection:
        if self.is_postgres:
            return PostgresConnection(self.url.replace("postgres://", "postgresql://", 1))
        if self.sqlite_path is None:
            raise RuntimeError("Database needs a sqlite_path or a postgres url")
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        # 30s so the single background job worker and API requests wait for each
        # other's writes instead of failing with "database is locked".
        conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def ensure_schema(self) -> None:
        """Create tables and run migrations, once per process."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            conn = self._open()
            try:
                conn.executescript(_DDL)
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()
            self._schema_ready = True

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        """A transaction: commits on success, rolls back on error, always closes."""
        self.ensure_schema()
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def use(self, conn: Connection | None = None) -> Iterator[Connection]:
        """Reuse a caller's connection (so several repository calls share one transaction),
        or open a new one."""
        if conn is not None:
            yield conn
        else:
            with self.connection() as opened:
                yield opened

    # ── Migrations ───────────────────────────────────────────────────────────

    def _migrate(self, conn: Connection) -> None:
        if isinstance(conn, PostgresConnection):
            # Same column set as SQLite; ADD COLUMN IF NOT EXISTS makes each one a no-op when a
            # table already has it.
            conn.execute(
                "ALTER TABLE scripts ADD COLUMN IF NOT EXISTS exam_id TEXT "
                "REFERENCES exams(exam_id) ON DELETE SET NULL"
            )
            for table, column, definition in _ADDED_COLUMNS:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
            for column, definition in _EXTRACTION_COLUMNS:
                conn.execute(
                    f"ALTER TABLE question_extractions ADD COLUMN IF NOT EXISTS {column} "
                    f"{definition}"
                )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_exam ON scripts(exam_id)")
            self._backfill_normalized_questions(conn)
            return

        for table, column, definition in _ADDED_COLUMNS:
            self._add_column(conn, table, column, definition)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_exam ON scripts(exam_id)")

        self._migrate_question_crops(conn)
        for column, definition in _EXTRACTION_COLUMNS:
            self._add_column(conn, "question_extractions", column, definition)
        self._backfill_normalized_questions(conn)

    @staticmethod
    def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _migrate_question_crops(conn: sqlite3.Connection) -> None:
        """The per-question crop approach was retired; carry its rows into
        question_extractions."""
        legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_crops'"
        ).fetchone()
        if not legacy:
            return
        current = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_extractions'"
        ).fetchone()
        if not current:  # pragma: no cover - DDL always creates question_extractions first
            conn.execute("ALTER TABLE question_crops RENAME TO question_extractions")
            conn.execute("ALTER TABLE question_extractions RENAME COLUMN crop_id TO extraction_id")
            conn.execute("ALTER TABLE question_extractions RENAME COLUMN crop_path TO source_path")
            return
        conn.execute(
            """
            INSERT OR IGNORE INTO question_extractions
                (extraction_id, script_id, question_id, question_number, question_type,
                 source_path, page_number, extracted_text, extraction_method,
                 ocr_confidence, vlm_confidence, extraction_confidence, needs_review,
                 review_reason, created_at)
            SELECT crop_id, script_id, question_id, question_number, question_type,
                   crop_path, page_number, extracted_text, extraction_method,
                   ocr_confidence, vlm_confidence, extraction_confidence, needs_review,
                   review_reason, created_at
            FROM question_crops legacy
            WHERE EXISTS (
                SELECT 1 FROM scripts WHERE scripts.script_id = legacy.script_id
            )
            """
        )
        conn.execute("DROP TABLE question_crops")

    @staticmethod
    def _backfill_normalized_questions(conn: Connection) -> None:
        """Populate the normalized authoring tables from legacy exams.config_json, once
        per question."""
        for exam in conn.execute("SELECT exam_id, config_json FROM exams").fetchall():
            questions: list[dict[str, Any]] = json.loads(exam["config_json"] or "{}").get(
                "questions", []
            )
            for number, question in enumerate(questions, start=1):
                question_id = str(question.get("question_id") or f"q{number}").strip()
                exists = conn.execute(
                    "SELECT 1 FROM exam_questions WHERE exam_id=? AND question_id=?",
                    (exam["exam_id"], question_id),
                ).fetchone()
                if exists:
                    continue
                question_row_id = uuid4().hex
                conn.execute(
                    """
                    INSERT INTO exam_questions
                        (question_row_id, exam_id, question_id, question_number, question_text,
                         question_type, golden_answer, max_marks, review_confidence_threshold)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        question_row_id,
                        exam["exam_id"],
                        question_id,
                        number,
                        question.get("question_text", ""),
                        question.get("question_type") or "short_answer",
                        question.get("golden_answer", ""),
                        question.get("max_marks", 1),
                        question.get("review_confidence_threshold"),
                    ),
                )
                for index, criterion in enumerate(question.get("criteria", [])):
                    conn.execute(
                        """
                        INSERT INTO rubric_criteria
                            (criterion_id, question_row_id, name, max_marks,
                             expected_concepts, guidance, display_order)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            question_row_id,
                            criterion.get("name", f"Criterion {index + 1}"),
                            criterion.get("marks", 0),
                            json.dumps(criterion.get("expected_concepts", [])),
                            criterion.get("guidance", ""),
                            index,
                        ),
                    )
                for index, option in enumerate(question.get("options", [])):
                    conn.execute(
                        """
                        INSERT INTO question_options
                            (option_id, question_row_id, option_key, option_text,
                             is_correct, display_order)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            question_row_id,
                            option.get("option_key", str(index + 1)),
                            option.get("option_text", ""),
                            int(bool(option.get("is_correct"))),
                            option.get("display_order", index),
                        ),
                    )


# Process-wide default, built from settings. The API wires its own through the container
# (app/container.py); this exists for one-off maintenance scripts that run raw SQL.
default_database = Database.from_settings(settings)


def get_db() -> AbstractContextManager[Connection]:
    """A transaction on the default database, for maintenance scripts that run raw SQL."""
    return default_database.connection()
