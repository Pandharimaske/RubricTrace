"""Exam-level persistence: exams, their answer key, per-exam summaries, results and jobs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from backend.app.db.database import (
    DEFAULT_REVIEW_THRESHOLD,
    clamp_review_threshold,
    effective_status,
    get_db,
)

# ── Exams ────────────────────────────────────────────────────────────────────

_SUMMARY_SQL = """
SELECT e.*,
  (SELECT COUNT(*) FROM scripts s WHERE s.exam_id = e.exam_id) AS script_count,
  (SELECT COUNT(*) FROM scripts s WHERE s.exam_id = e.exam_id
     AND s.status IN ('processed', 'graded', 'needs_review')) AS processed_count,
  (SELECT COUNT(*) FROM scripts s WHERE s.exam_id = e.exam_id
     AND s.status = 'error') AS error_count,
  (SELECT COUNT(DISTINCT qe.script_id) FROM question_evaluations qe
     JOIN scripts s ON s.script_id = qe.script_id
     WHERE s.exam_id = e.exam_id) AS graded_count,
  -- An answer needs review when the teacher hasn't approved it and its stored
  -- confidence is below this exam's threshold (decided at read time).
  (SELECT COUNT(*) FROM question_evaluations qe
     JOIN scripts s ON s.script_id = qe.script_id
     WHERE s.exam_id = e.exam_id AND qe.status != 'teacher_approved'
    AND qe.confidence < COALESCE(qe.threshold_used, e.review_confidence_threshold)) AS review_count
FROM exams e
"""


def _normalized_questions(conn: sqlite3.Connection, exam_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM exam_questions WHERE exam_id=? ORDER BY question_number", (exam_id,)
    ).fetchall()
    questions = []
    for row in rows:
        question = dict(row)
        question.pop("question_row_id", None)
        question.pop("exam_id", None)
        question.pop("created_at", None)
        question.pop("updated_at", None)
        criteria = conn.execute(
            "SELECT name, description, max_marks AS marks, expected_concepts, guidance "
            "FROM rubric_criteria WHERE question_row_id=? ORDER BY display_order",
            (row["question_row_id"],),
        ).fetchall()
        question["criteria"] = [
            {
                **dict(criterion),
                "expected_concepts": json.loads(criterion["expected_concepts"] or "[]"),
            }
            for criterion in criteria
        ]
        options = conn.execute(
            "SELECT option_key, option_text, is_correct, display_order "
            "FROM question_options WHERE question_row_id=? ORDER BY display_order",
            (row["question_row_id"],),
        ).fetchall()
        question["options"] = [
            {**dict(option), "is_correct": bool(option["is_correct"])} for option in options
        ]
        questions.append(question)
    return questions


def _summarize(
    row: sqlite3.Row, include_questions: bool, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    d = dict(row)
    d["review_confidence_threshold"] = clamp_review_threshold(
        d.get("review_confidence_threshold", DEFAULT_REVIEW_THRESHOLD)
    )
    questions = (
        _normalized_questions(conn, d["exam_id"])
        if conn is not None
        else json.loads(d.pop("config_json", None) or "{}").get("questions", [])
    )
    d.pop("config_json", None)
    d["question_count"] = len(questions)
    d["total_marks"] = round(sum(q.get("max_marks", 0) for q in questions), 2)
    d["answer_key_complete"] = bool(questions) and all(
        (q.get("golden_answer") or "").strip() for q in questions
    )
    if include_questions:
        d["questions"] = questions
    return d


def create_exam(name: str) -> dict[str, Any]:
    exam_id = uuid4().hex
    with get_db() as conn:
        conn.execute("INSERT INTO exams(exam_id, name) VALUES (?, ?)", (exam_id, name.strip()))
        return get_exam(conn, exam_id)  # type: ignore[return-value]


def get_exam(conn: sqlite3.Connection, exam_id: str) -> dict[str, Any] | None:
    row = conn.execute(_SUMMARY_SQL + " WHERE e.exam_id = ?", (exam_id,)).fetchone()
    return _summarize(row, include_questions=True, conn=conn) if row else None


def list_exams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(_SUMMARY_SQL + " ORDER BY e.created_at DESC").fetchall()
    return [_summarize(r, include_questions=False, conn=conn) for r in rows]


def replace_exam_questions(
    conn: sqlite3.Connection, exam_id: str, questions: list[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM exam_questions WHERE exam_id=?", (exam_id,))
    for number, question in enumerate(questions, start=1):
        question_row_id = uuid4().hex
        conn.execute(
            """
            INSERT INTO exam_questions
                (question_row_id, exam_id, question_id, question_number, question_text,
                 question_type, golden_answer, max_marks, review_confidence_threshold,
                 evaluator_config_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_row_id,
                exam_id,
                question["question_id"],
                number,
                question.get("question_text", ""),
                question["question_type"],
                question.get("golden_answer", ""),
                question["max_marks"],
                question.get("review_confidence_threshold"),
                question.get("evaluator_config_id"),
            ),
        )
        for index, criterion in enumerate(question.get("criteria", [])):
            conn.execute(
                """
                INSERT INTO rubric_criteria
                    (criterion_id, question_row_id, name, description, max_marks,
                     expected_concepts, guidance, display_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    question_row_id,
                    criterion["name"],
                    criterion.get("description", ""),
                    criterion["marks"],
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
                    option["option_key"],
                    option["option_text"],
                    int(option.get("is_correct", False)),
                    option.get("display_order", index),
                ),
            )


def create_evaluator_config(config: dict[str, Any]) -> dict[str, Any]:
    config_id = uuid4().hex
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO evaluator_configs
                (evaluator_config_id, name, evaluator_kind, provider_name, model_name,
                 temperature, max_tokens, fallback_config_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config_id,
                config["name"].strip(),
                config["evaluator_kind"],
                config["provider_name"].strip(),
                config["model_name"].strip(),
                config.get("temperature", 0.0),
                config.get("max_tokens"),
                config.get("fallback_config_id"),
            ),
        )
        row = conn.execute(
            "SELECT * FROM evaluator_configs WHERE evaluator_config_id=?", (config_id,)
        ).fetchone()
        return dict(row)


def list_evaluator_configs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM evaluator_configs ORDER BY name COLLATE NOCASE"
        ).fetchall()
    ]


def update_exam(
    exam_id: str,
    name: str | None = None,
    questions: list[dict[str, Any]] | None = None,
    review_confidence_threshold: float | None = None,
) -> dict[str, Any] | None:
    with get_db() as conn:
        exam = get_exam(conn, exam_id)
        if not exam:
            return None
        threshold = (
            clamp_review_threshold(review_confidence_threshold)
            if review_confidence_threshold is not None
            else exam["review_confidence_threshold"]
        )
        conn.execute(
            "UPDATE exams SET name=?, config_json=?, review_confidence_threshold=?, "
            "updated_at=datetime('now') WHERE exam_id=?",
            (
                name.strip() if name is not None else exam["name"],
                json.dumps(
                    {"questions": questions if questions is not None else exam["questions"]}
                ),
                threshold,
                exam_id,
            ),
        )
        if questions is not None:
            replace_exam_questions(conn, exam_id, questions)
        # Moving the threshold changes which answers count as flagged, so bring the
        # stored per-script status (used by the scripts list) back in line.
        if review_confidence_threshold is not None:
            refresh_exam_script_statuses(conn, exam_id, threshold)
        return get_exam(conn, exam_id)


def refresh_exam_script_statuses(
    conn: sqlite3.Connection, exam_id: str, threshold: float | None = None
) -> None:
    """Set each graded script's stored status to 'needs_review' or 'graded' under the threshold."""
    if threshold is None:
        row = conn.execute(
            "SELECT review_confidence_threshold FROM exams WHERE exam_id=?", (exam_id,)
        ).fetchone()
        threshold = clamp_review_threshold(row[0]) if row else DEFAULT_REVIEW_THRESHOLD
    conn.execute(
        """
        UPDATE scripts SET status = CASE
            WHEN EXISTS (
                SELECT 1 FROM question_evaluations qe
                WHERE qe.script_id = scripts.script_id
                  AND qe.status != 'teacher_approved' AND qe.confidence < ?
            ) THEN 'needs_review' ELSE 'graded' END
        WHERE exam_id = ?
          AND status IN ('graded', 'needs_review')
          AND EXISTS (SELECT 1 FROM question_evaluations qe WHERE qe.script_id = scripts.script_id)
        """,
        (threshold, exam_id),
    )


def delete_exam(exam_id: str) -> list[str] | None:
    """Delete an exam and everything under it.

    `scripts.exam_id` has no ON DELETE CASCADE (it was added via a later
    migration), so scripts must be removed before the exam row itself or the
    foreign key check fails. Deleting a script cascades to its question_extractions
    and question_evaluations. `jobs` does have ON DELETE CASCADE on exam_id,
    so those go automatically when the exam row is removed.

    Returns the script_ids that were deleted (so their files can be removed
    from disk), or None if no exam with this id existed.
    """
    with get_db() as conn:
        exam = get_exam(conn, exam_id)
        if not exam:
            return None
        script_ids = [
            r["script_id"]
            for r in conn.execute("SELECT script_id FROM scripts WHERE exam_id = ?", (exam_id,))
        ]
        conn.execute("DELETE FROM scripts WHERE exam_id = ?", (exam_id,))
        conn.execute("DELETE FROM exams WHERE exam_id = ?", (exam_id,))
        return script_ids


# ── Scripts and results within an exam ───────────────────────────────────────


def exam_scripts(conn: sqlite3.Connection, exam_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sc.script_id, sc.student_id, sc.filename, sc.status, sc.page_count,
               sc.error, sc.created_at, st.name AS student_name,
               COUNT(qe.eval_id) AS graded_questions,
               COALESCE(SUM(qe.awarded_marks), 0) AS total_awarded,
               COALESCE(SUM(qe.max_marks), 0) AS total_max,
               COALESCE(SUM(CASE WHEN qe.status != 'teacher_approved'
                                  AND qe.confidence < COALESCE(qe.threshold_used, ex.review_confidence_threshold)
                                 THEN 1 ELSE 0 END), 0)
                   AS needs_review_count,
               (SELECT COUNT(*) FROM question_extractions qe
                WHERE qe.script_id = sc.script_id) AS extraction_count
        FROM scripts sc
        LEFT JOIN exams ex ON ex.exam_id = sc.exam_id
        LEFT JOIN students st ON st.student_id = sc.student_id
        LEFT JOIN question_evaluations qe ON qe.script_id = sc.script_id
        WHERE sc.exam_id = ?
        GROUP BY sc.script_id
        ORDER BY st.name COLLATE NOCASE, sc.created_at
        """,
        (exam_id,),
    ).fetchall()
    scripts = []
    for r in rows:
        d = dict(r)
        # The stored status goes stale once the teacher approves flagged answers,
        # so derive it from the evaluations for anything that has been graded.
        if d["graded_questions"] > 0 and d["status"] not in ("processing", "error"):
            d["status"] = "needs_review" if d["needs_review_count"] > 0 else "graded"
        scripts.append(d)
    return scripts


def exam_results(conn: sqlite3.Connection, exam: dict[str, Any]) -> dict[str, Any]:
    """Student × question grid with totals, ready for the results table and CSV."""
    questions = exam["questions"]
    question_ids = [q["question_id"] for q in questions]
    max_by_question = {q["question_id"]: q["max_marks"] for q in questions}
    total_max = round(sum(max_by_question.values()), 2)
    threshold = exam.get("review_confidence_threshold", DEFAULT_REVIEW_THRESHOLD)

    marks_by_script: dict[str, dict[str, dict[str, Any]]] = {}
    for r in conn.execute(
        """
        SELECT qe.script_id, qe.question_id, qe.awarded_marks, qe.max_marks,
             qe.status, qe.confidence, qe.threshold_used
        FROM question_evaluations qe
        JOIN scripts s ON s.script_id = qe.script_id
        WHERE s.exam_id = ?
        """,
        (exam["exam_id"],),
    ):
        if r["question_id"] in max_by_question:  # ignore questions removed from the key
            marks_by_script.setdefault(r["script_id"], {})[r["question_id"]] = {
                "awarded": r["awarded_marks"],
                "max": r["max_marks"],
                # needs_review vs scored is decided now, against the exam's threshold
                "status": effective_status(
                    r["status"], r["confidence"], r["threshold_used"] or threshold
                ),
                "confidence": r["confidence"],
            }

    rows = []
    for s in exam_scripts(conn, exam["exam_id"]):
        marks = marks_by_script.get(s["script_id"], {})
        awarded = round(sum(m["awarded"] for m in marks.values()), 2)
        pending = sum(1 for m in marks.values() if m["status"] == "needs_review")
        rows.append(
            {
                "script_id": s["script_id"],
                "student_id": s["student_id"],
                "student_name": s["student_name"],
                "filename": s["filename"],
                "status": s["status"],
                "marks": marks,
                "total_awarded": awarded,
                "total_max": total_max,
                "percentage": round(100 * awarded / total_max, 1) if total_max else None,
                "graded_questions": len(marks),
                "needs_review_count": pending,
                "final": bool(question_ids) and len(marks) == len(question_ids) and pending == 0,
            }
        )
    return {
        "exam_id": exam["exam_id"],
        "name": exam["name"],
        "question_ids": question_ids,
        "max_by_question": max_by_question,
        "total_max": total_max,
        "rows": rows,
    }


# ── Jobs ─────────────────────────────────────────────────────────────────────

_JOB_FIELDS = {"status", "total", "done", "message", "error"}


def _job(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    d["cancel_requested"] = bool(d["cancel_requested"])
    return d


def create_job(exam_id: str, kind: str, total: int) -> dict[str, Any]:
    job_id = uuid4().hex
    with get_db() as conn:
        conn.execute(
            "INSERT INTO jobs(job_id, exam_id, kind, total) VALUES (?, ?, ?, ?)",
            (job_id, exam_id, kind, total),
        )
        return _job(conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())  # type: ignore[return-value]


def active_job(conn: sqlite3.Connection, exam_id: str) -> dict[str, Any] | None:
    return _job(
        conn.execute(
            "SELECT * FROM jobs WHERE exam_id=? AND status IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            (exam_id,),
        ).fetchone()
    )


def latest_job(conn: sqlite3.Connection, exam_id: str) -> dict[str, Any] | None:
    return _job(
        conn.execute(
            "SELECT * FROM jobs WHERE exam_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (exam_id,),
        ).fetchone()
    )


def update_job(job_id: str, **fields: Any) -> None:
    sets = {k: v for k, v in fields.items() if k in _JOB_FIELDS}
    if not sets:
        return
    assignments = ", ".join(f"{k}=?" for k in sets)  # keys are whitelisted above
    with get_db() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments}, updated_at=datetime('now') WHERE job_id=?",
            (*sets.values(), job_id),
        )


def request_cancel(exam_id: str) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET cancel_requested=1, updated_at=datetime('now') "
            "WHERE exam_id=? AND status IN ('queued', 'running')",
            (exam_id,),
        )
        return cur.rowcount > 0


def is_cancel_requested(job_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return bool(row and row["cancel_requested"])


def mark_interrupted_jobs() -> None:
    """Jobs left 'running' by a server restart can never finish; close them out."""
    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error='Interrupted by a server restart', "
            "updated_at=datetime('now') WHERE status IN ('queued', 'running')"
        )
