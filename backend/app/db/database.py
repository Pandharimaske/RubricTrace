"""
SQLite persistence layer for RubricTrace.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from backend.app.core.settings import DATA_DIR, DATABASE_URL

DB_PATH = DATA_DIR / "rubrictrace.db"


# ── Review threshold ─────────────────────────────────────────────────────────
# Confidence is stored per answer; whether an answer "needs review" is decided
# at READ time by comparing it with the exam's review threshold
# (exams.review_confidence_threshold). The threshold is part of the exam's
# configuration: the teacher sets it with the answer key (Exam -> Answer key) and
# it applies uniformly to that exam. It is deliberately NOT adjustable from the
# review queue -- changing it is an explicit configuration update, and it never
# needs a re-grade. A teacher-approved answer is never flagged again, whatever
# its confidence.

DEFAULT_REVIEW_THRESHOLD = 0.65
MIN_REVIEW_THRESHOLD = 0.05
MAX_REVIEW_THRESHOLD = 1.0

# Reasons that were derived from confidence when the answer was graded. They
# depend on the threshold, so they're rebuilt at read time instead of trusted.
_CONFIDENCE_FLAG_PREFIXES = ("Very low confidence", "Low confidence", "LLM flagged for review")


def clamp_review_threshold(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_THRESHOLD
    return round(max(MIN_REVIEW_THRESHOLD, min(MAX_REVIEW_THRESHOLD, number)), 2)


def effective_status(status: str, confidence: float | None, threshold: float) -> str:
    """Status as the teacher should see it under the current threshold."""
    if status == "teacher_approved":
        return status
    return "needs_review" if (confidence or 0.0) < threshold else "scored"


def review_verdict(confidence: float | None, threshold: float) -> tuple[bool, str]:
    """(needs_review, reason) for a single confidence under a threshold.

    The one place the "below threshold" rule lives for code that has no exam row to
    read from (the offline grading scripts, the /score endpoint, the pipeline).
    """
    conf = confidence or 0.0
    if conf < threshold:
        return True, f"Low confidence ({conf:.2f}) is below the review threshold ({threshold:.2f})."
    return False, ""


def effective_flag_reasons(
    stored: list[str] | None, confidence: float | None, threshold: float, status: str
) -> list[str]:
    """Grade-time reasons that don't depend on confidence, plus a live confidence reason."""
    reasons = [r for r in (stored or []) if not r.startswith(_CONFIDENCE_FLAG_PREFIXES)]
    conf = confidence or 0.0
    if status != "teacher_approved" and conf < threshold:
        reasons.append(f"Confidence {conf:.0%} is below the review threshold ({threshold:.0%})")
    return reasons


def review_threshold_for_script(conn: sqlite3.Connection, script_id: str) -> float:
    row = conn.execute(
        "SELECT e.review_confidence_threshold AS t FROM scripts s "
        "LEFT JOIN exams e ON e.exam_id = s.exam_id WHERE s.script_id = ?",
        (script_id,),
    ).fetchone()
    return (
        clamp_review_threshold(row["t"])
        if row and row["t"] is not None
        else DEFAULT_REVIEW_THRESHOLD
    )


class _PostgresConnection:
    """Small DB-API compatibility layer for the existing repository functions."""

    def __init__(self, url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL requires the psycopg[binary] dependency") from exc
        self._connection = psycopg.connect(url, row_factory=dict_row)

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

    def execute(self, sql: str, parameters: tuple | list = ()):
        return self._connection.execute(self._adapt_sql(sql), parameters)

    def executemany(self, sql: str, parameters):
        return self._connection.executemany(self._adapt_sql(sql), parameters)

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


def _connect() -> sqlite3.Connection | _PostgresConnection:
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        return _PostgresConnection(DATABASE_URL.replace("postgres://", "postgresql://", 1))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    _migrate(conn)
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection | _PostgresConnection, None, None]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
CREATE INDEX IF NOT EXISTS idx_scripts_exam ON scripts(exam_id);

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
    question_type               TEXT NOT NULL CHECK (question_type IN ('mcq', 'true_false', 'short_answer', 'long_answer')),
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


def _migrate(conn: sqlite3.Connection | _PostgresConnection) -> None:
    """Add columns that older databases don't have yet."""
    if isinstance(conn, _PostgresConnection):
        conn.execute(
            "ALTER TABLE scripts ADD COLUMN IF NOT EXISTS exam_id TEXT REFERENCES exams(exam_id) ON DELETE SET NULL"
        )
        conn.execute("ALTER TABLE scripts ADD COLUMN IF NOT EXISTS error TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_exam ON scripts(exam_id)")
        return
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(scripts)")}
    if "exam_id" not in cols:
        conn.execute("ALTER TABLE scripts ADD COLUMN exam_id TEXT REFERENCES exams(exam_id)")
    if "error" not in cols:
        conn.execute("ALTER TABLE scripts ADD COLUMN error TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_exam ON scripts(exam_id)")

    eval_cols = {row["name"] for row in conn.execute("PRAGMA table_info(question_evaluations)")}
    if "question_text" not in eval_cols:
        conn.execute(
            "ALTER TABLE question_evaluations ADD COLUMN question_text TEXT NOT NULL DEFAULT ''"
        )
    if "page_number" not in eval_cols:
        conn.execute("ALTER TABLE question_evaluations ADD COLUMN page_number INTEGER")
    if "question_type" not in eval_cols:
        conn.execute(
            "ALTER TABLE question_evaluations ADD COLUMN question_type TEXT NOT NULL DEFAULT 'unknown'"
        )
    if "answer_text" not in eval_cols:
        conn.execute(
            "ALTER TABLE question_evaluations ADD COLUMN answer_text TEXT NOT NULL DEFAULT ''"
        )
    if "threshold_used" not in eval_cols:
        conn.execute(
            "ALTER TABLE question_evaluations ADD COLUMN threshold_used REAL NOT NULL DEFAULT 0.65"
        )
    if "criteria_scores_json" not in eval_cols:
        conn.execute(
            "ALTER TABLE question_evaluations ADD COLUMN criteria_scores_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "evaluator_snapshot_json" not in eval_cols:
        conn.execute("ALTER TABLE question_evaluations ADD COLUMN evaluator_snapshot_json TEXT")

    exam_cols = {row["name"] for row in conn.execute("PRAGMA table_info(exams)")}
    if "review_confidence_threshold" not in exam_cols:
        conn.execute(
            "ALTER TABLE exams ADD COLUMN review_confidence_threshold REAL NOT NULL DEFAULT 0.65"
        )

    legacy_extractions = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_crops'"
    ).fetchone()
    current_extractions = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_extractions'"
    ).fetchone()
    if legacy_extractions and not current_extractions:
        conn.execute("ALTER TABLE question_crops RENAME TO question_extractions")
        conn.execute("ALTER TABLE question_extractions RENAME COLUMN crop_id TO extraction_id")
        conn.execute("ALTER TABLE question_extractions RENAME COLUMN crop_path TO source_path")
    elif legacy_extractions and current_extractions:
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

    crop_cols = {row["name"] for row in conn.execute("PRAGMA table_info(question_extractions)")}
    if "question_number" not in crop_cols:
        conn.execute("ALTER TABLE question_extractions ADD COLUMN question_number INTEGER")
    if "question_type" not in crop_cols:
        conn.execute(
            "ALTER TABLE question_extractions ADD COLUMN question_type TEXT NOT NULL DEFAULT 'unknown'"
        )
    if "page_number" not in crop_cols:
        conn.execute("ALTER TABLE question_extractions ADD COLUMN page_number INTEGER")
    if "vlm_confidence" not in crop_cols:
        conn.execute("ALTER TABLE question_extractions ADD COLUMN vlm_confidence REAL")
    if "extraction_confidence" not in crop_cols:
        conn.execute("ALTER TABLE question_extractions ADD COLUMN extraction_confidence REAL")
    if "needs_review" not in crop_cols:
        conn.execute(
            "ALTER TABLE question_extractions ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0"
        )
    if "review_reason" not in crop_cols:
        conn.execute(
            "ALTER TABLE question_extractions ADD COLUMN review_reason TEXT NOT NULL DEFAULT ''"
        )

    _migrate_normalized_exam_data(conn)


def _migrate_normalized_exam_data(conn: sqlite3.Connection) -> None:
    """Backfill normalized authoring tables from legacy exam JSON once per question."""
    exam_rows = conn.execute("SELECT exam_id, config_json FROM exams").fetchall()
    for exam in exam_rows:
        questions = json.loads(exam["config_json"] or "{}").get("questions", [])
        for number, question in enumerate(questions, start=1):
            question_id = str(question.get("question_id") or f"q{number}").strip()
            question_row_id = uuid4().hex
            exists = conn.execute(
                "SELECT 1 FROM exam_questions WHERE exam_id=? AND question_id=?",
                (exam["exam_id"], question_id),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO exam_questions
                    (question_row_id, exam_id, question_id, question_number, question_text, question_type,
                     golden_answer, max_marks, review_confidence_threshold)
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
                        (criterion_id, question_row_id, name, max_marks, expected_concepts, guidance, display_order)
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
                        (option_id, question_row_id, option_key, option_text, is_correct, display_order)
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


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(_DDL)


# --- Students ---


def upsert_student(student_id: str, name: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO students(student_id, name) VALUES (?, ?)",
            (student_id, name),
        )


def list_students(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.student_id, s.name, s.created_at, COUNT(sc.script_id) AS script_count "
        "FROM students s LEFT JOIN scripts sc ON s.student_id=sc.student_id "
        "GROUP BY s.student_id ORDER BY s.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_student(conn: sqlite3.Connection, student_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
    return dict(row) if row else None


# --- Scripts ---


def insert_script(
    script_id: str,
    student_id: str,
    filename: str,
    file_path: str,
    exam_id: str | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO scripts(script_id, student_id, filename, file_path, exam_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (script_id, student_id, filename, file_path, exam_id),
        )


def update_script_status(script_id: str, status: str, page_count: int | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE scripts SET status=?, page_count=? WHERE script_id=?",
            (status, page_count, script_id),
        )


def get_script(conn: sqlite3.Connection, script_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM scripts WHERE script_id=?", (script_id,)).fetchone()
    return dict(row) if row else None


def list_scripts_for_student(conn: sqlite3.Connection, student_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM scripts WHERE student_id=? ORDER BY created_at DESC", (student_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_scripts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT sc.*, st.name AS student_name, e.name AS exam_name FROM scripts sc "
        "LEFT JOIN students st ON sc.student_id=st.student_id "
        "LEFT JOIN exams e ON sc.exam_id=e.exam_id "
        "ORDER BY sc.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# --- Question extractions ---


def upsert_question_extraction(
    extraction_id: str,
    script_id: str,
    question_id: str,
    source_path: str | None,
    extracted_text: str,
    extraction_method: str,
    ocr_confidence: float | None = None,
    page_number: int | None = None,
    question_number: int | None = None,
    question_type: str = "unknown",
    vlm_confidence: float | None = None,
    extraction_confidence: float | None = None,
    needs_review: bool = False,
    review_reason: str = "",
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO question_extractions
                (extraction_id, script_id, question_id, question_number, question_type, source_path,
                 page_number, extracted_text, extraction_method, ocr_confidence, vlm_confidence,
                 extraction_confidence, needs_review, review_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(script_id, question_id) DO UPDATE SET
                question_number=excluded.question_number,
                question_type=excluded.question_type,
                source_path=excluded.source_path,
                page_number=excluded.page_number,
                extracted_text=excluded.extracted_text,
                extraction_method=excluded.extraction_method,
                ocr_confidence=excluded.ocr_confidence,
                vlm_confidence=excluded.vlm_confidence,
                extraction_confidence=excluded.extraction_confidence,
                needs_review=excluded.needs_review,
                review_reason=excluded.review_reason
            """,
            (
                extraction_id,
                script_id,
                question_id,
                question_number,
                question_type,
                source_path,
                page_number,
                extracted_text,
                extraction_method,
                ocr_confidence,
                vlm_confidence,
                extraction_confidence,
                int(needs_review),
                review_reason,
            ),
        )


def get_extractions_for_script(conn: sqlite3.Connection, script_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM question_extractions WHERE script_id=? ORDER BY question_id", (script_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_extraction(
    conn: sqlite3.Connection, script_id: str, question_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM question_extractions WHERE script_id=? AND question_id=?",
        (script_id, question_id),
    ).fetchone()
    return dict(row) if row else None


# --- Evaluations ---


def upsert_evaluation(
    eval_id: str,
    script_id: str,
    question_id: str,
    golden_answer: str,
    rubric: list[dict[str, Any]],
    max_marks: float,
    awarded_marks: float,
    confidence: float,
    status: str,
    evidence: list[str],
    reasoning: str,
    llm_reasoning: dict[str, Any] | None = None,
    llm_model: str | None = None,
    question_text: str = "",
    question_type: str = "unknown",
    answer_text: str = "",
    page_number: int | None = None,
    threshold_used: float = DEFAULT_REVIEW_THRESHOLD,
    criteria_scores: list[dict[str, Any]] | None = None,
    evaluator_snapshot: dict[str, Any] | None = None,
) -> None:
    # needs_review is not stored: it's derived at read time from confidence vs the
    # exam's threshold (effective_status above). The legacy needs_review column keeps
    # its default and is ignored. `status` only matters here for 'teacher_approved'.
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO question_evaluations
                (eval_id, script_id, question_id, question_text, question_type, answer_text,
                 page_number, golden_answer, rubric_json, max_marks, awarded_marks, confidence,
                 threshold_used, criteria_scores_json, status, evidence_json, reasoning,
                 llm_reasoning_json, llm_model, evaluator_snapshot_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(script_id, question_id) DO UPDATE SET
                question_text=excluded.question_text,
                question_type=excluded.question_type,
                answer_text=excluded.answer_text,
                page_number=excluded.page_number,
                golden_answer=excluded.golden_answer,
                rubric_json=excluded.rubric_json,
                max_marks=excluded.max_marks,
                awarded_marks=excluded.awarded_marks,
                confidence=excluded.confidence,
                threshold_used=excluded.threshold_used,
                criteria_scores_json=excluded.criteria_scores_json,
                status=excluded.status,
                evidence_json=excluded.evidence_json,
                reasoning=excluded.reasoning,
                llm_reasoning_json=excluded.llm_reasoning_json,
                llm_model=excluded.llm_model,
                evaluator_snapshot_json=excluded.evaluator_snapshot_json,
                teacher_override=NULL,
                teacher_override_reason=NULL,
                updated_at=datetime('now')
            """,
            (
                eval_id,
                script_id,
                question_id,
                question_text,
                question_type,
                answer_text,
                page_number,
                golden_answer,
                json.dumps(rubric),
                max_marks,
                awarded_marks,
                confidence,
                threshold_used,
                json.dumps(criteria_scores or []),
                status,
                json.dumps(evidence),
                reasoning,
                json.dumps(llm_reasoning) if llm_reasoning else None,
                llm_model,
                json.dumps(evaluator_snapshot) if evaluator_snapshot else None,
            ),
        )


def apply_override(script_id: str, question_id: str, awarded_marks: float, reason: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE question_evaluations
            SET awarded_marks=?, teacher_override=?, teacher_override_reason=?,
                status='teacher_approved', updated_at=datetime('now')
            WHERE script_id=? AND question_id=?
            """,
            (awarded_marks, awarded_marks, reason, script_id, question_id),
        )
        return cursor.rowcount > 0


def get_evaluations_for_script(conn: sqlite3.Connection, script_id: str) -> list[dict[str, Any]]:
    threshold = review_threshold_for_script(conn, script_id)
    rows = conn.execute(
        "SELECT * FROM question_evaluations WHERE script_id=? ORDER BY question_id", (script_id,)
    ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        d["rubric"] = json.loads(d.pop("rubric_json", "[]"))
        d["evidence"] = json.loads(d.pop("evidence_json", "[]"))
        d["criteria_scores"] = json.loads(d.pop("criteria_scores_json", "[]"))
        evaluator_raw = d.get("evaluator_snapshot_json")
        d["evaluator_snapshot"] = json.loads(evaluator_raw) if evaluator_raw else None
        d.pop("evaluator_snapshot_json", None)
        llm_raw = d.get("llm_reasoning_json")
        d["llm_reasoning"] = json.loads(llm_raw) if llm_raw else None
        d.pop("llm_reasoning_json", None)

        # needs_review / status are derived from confidence vs the exam's threshold
        # here, not read from the columns written at grading time (see top of file).
        evaluation_threshold = clamp_review_threshold(d.get("threshold_used", threshold))
        d["status"] = effective_status(d["status"], d["confidence"], evaluation_threshold)
        d["needs_review"] = d["status"] == "needs_review"
        d["review_threshold"] = evaluation_threshold
        stored_reasons = (
            (d["llm_reasoning"] or {}).get("flag_reasons") if d["llm_reasoning"] else None
        )
        reasons = effective_flag_reasons(
            stored_reasons, d["confidence"], evaluation_threshold, d["status"]
        )
        if isinstance(d["llm_reasoning"], dict):
            d["llm_reasoning"]["flag_reasons"] = reasons
        d["flag_reasons"] = reasons
        results.append(d)
    return results


def get_script_totals(conn: sqlite3.Connection, script_id: str) -> dict[str, Any]:
    threshold = review_threshold_for_script(conn, script_id)
    row = conn.execute(
        """
        SELECT
            COUNT(*) as total_questions,
            COALESCE(SUM(awarded_marks), 0) as total_awarded,
            COALESCE(SUM(max_marks), 0) as total_max,
            COALESCE(SUM(CASE WHEN status != 'teacher_approved' AND confidence < COALESCE(threshold_used, ?)
                              THEN 1 ELSE 0 END), 0) as needs_review_count,
            COALESCE(SUM(CASE WHEN status='teacher_approved' THEN 1 ELSE 0 END), 0) as approved_count
        FROM question_evaluations WHERE script_id=?
        """,
        (threshold, script_id),
    ).fetchone()
    return dict(row) if row else {}
