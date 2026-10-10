"""Exam-level persistence: exams and their answer key, evaluator configs, per-exam summaries
and results, and background jobs."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from backend.app.db.database import Connection, Database
from backend.app.models.domain import ReviewPolicy

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
  -- Needs review: not teacher-approved and below the threshold stored with the grade.
  (SELECT COUNT(*) FROM question_evaluations qe
     JOIN scripts s ON s.script_id = qe.script_id
     WHERE s.exam_id = e.exam_id AND qe.status != 'teacher_approved'
       AND qe.confidence < qe.threshold_used) AS review_count
FROM exams e
"""


class ExamRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Exams ────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalized_questions(conn: Connection, exam_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM exam_questions WHERE exam_id=? ORDER BY question_number", (exam_id,)
        ).fetchall()
        questions = []
        for row in rows:
            question = dict(row)
            for key in ("question_row_id", "exam_id", "created_at", "updated_at"):
                question.pop(key, None)
            criteria = conn.execute(
                "SELECT name, description, max_marks AS marks, expected_concepts, guidance "
                "FROM rubric_criteria WHERE question_row_id=? ORDER BY display_order",
                (row["question_row_id"],),
            ).fetchall()
            question["criteria"] = [
                {**dict(c), "expected_concepts": json.loads(c["expected_concepts"] or "[]")}
                for c in criteria
            ]
            options = conn.execute(
                "SELECT option_key, option_text, is_correct, display_order "
                "FROM question_options WHERE question_row_id=? ORDER BY display_order",
                (row["question_row_id"],),
            ).fetchall()
            question["options"] = [
                {**dict(o), "is_correct": bool(o["is_correct"])} for o in options
            ]
            questions.append(question)
        return questions

    def _summarize(
        self, row: sqlite3.Row, conn: Connection, include_questions: bool
    ) -> dict[str, Any]:
        d = dict(row)
        d["review_confidence_threshold"] = ReviewPolicy.clamp(
            d.get("review_confidence_threshold", ReviewPolicy.DEFAULT_THRESHOLD)
        )
        questions = self._normalized_questions(conn, d["exam_id"])
        d.pop("config_json", None)
        d["question_count"] = len(questions)
        d["total_marks"] = round(sum(q.get("max_marks", 0) for q in questions), 2)
        d["answer_key_complete"] = bool(questions) and all(
            (q.get("golden_answer") or "").strip() for q in questions
        )
        if include_questions:
            d["questions"] = questions
        return d

    def create(self, name: str) -> dict[str, Any]:
        exam_id = uuid4().hex
        with self.db.use() as c:
            c.execute("INSERT INTO exams(exam_id, name) VALUES (?, ?)", (exam_id, name.strip()))
            return self.get(exam_id, c)  # type: ignore[return-value]

    def get(self, exam_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        with self.db.use(conn) as c:
            row = c.execute(_SUMMARY_SQL + " WHERE e.exam_id = ?", (exam_id,)).fetchone()
            return self._summarize(row, c, include_questions=True) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            rows = c.execute(_SUMMARY_SQL + " ORDER BY e.created_at DESC").fetchall()
            return [self._summarize(r, c, include_questions=False) for r in rows]

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        for exam in self.list_all():
            if exam["name"] == name:
                return exam
        return None

    @staticmethod
    def _replace_questions(conn: Connection, exam_id: str, questions: list[dict[str, Any]]) -> None:
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
                        (option_id, question_row_id, option_key, option_text, is_correct,
                         display_order)
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

    @staticmethod
    def _sync_evaluation_thresholds(conn: Connection, exam_id: str, exam_threshold: float) -> None:
        """Point every stored grade at the threshold now configured for it (the question's
        own override, else the exam's), so changing a threshold takes effect without a
        re-grade, and refresh each graded script's stored status to match."""
        conn.execute(
            """
            UPDATE question_evaluations SET threshold_used = COALESCE(
                (SELECT q.review_confidence_threshold FROM exam_questions q
                  JOIN scripts s ON s.exam_id = q.exam_id
                 WHERE s.script_id = question_evaluations.script_id
                   AND q.exam_id = ? AND q.question_id = question_evaluations.question_id),
                ?)
            WHERE script_id IN (SELECT script_id FROM scripts WHERE exam_id = ?)
            """,
            (exam_id, exam_threshold, exam_id),
        )
        conn.execute(
            """
            UPDATE scripts SET status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM question_evaluations qe
                    WHERE qe.script_id = scripts.script_id
                      AND qe.status != 'teacher_approved' AND qe.confidence < qe.threshold_used
                ) THEN 'needs_review' ELSE 'graded' END
            WHERE exam_id = ?
              AND status IN ('graded', 'needs_review')
              AND EXISTS (SELECT 1 FROM question_evaluations qe
                          WHERE qe.script_id = scripts.script_id)
            """,
            (exam_id,),
        )

    def update(
        self,
        exam_id: str,
        name: str | None = None,
        questions: list[dict[str, Any]] | None = None,
        review_confidence_threshold: float | None = None,
    ) -> dict[str, Any] | None:
        with self.db.use() as c:
            exam = self.get(exam_id, c)
            if not exam:
                return None
            threshold = (
                ReviewPolicy.clamp(review_confidence_threshold)
                if review_confidence_threshold is not None
                else exam["review_confidence_threshold"]
            )
            kept_questions = questions if questions is not None else exam["questions"]
            c.execute(
                "UPDATE exams SET name=?, config_json=?, review_confidence_threshold=?, "
                "updated_at=datetime('now') WHERE exam_id=?",
                (
                    name.strip() if name is not None else exam["name"],
                    json.dumps({"questions": kept_questions}),
                    threshold,
                    exam_id,
                ),
            )
            if questions is not None:
                self._replace_questions(c, exam_id, questions)
            if questions is not None or review_confidence_threshold is not None:
                self._sync_evaluation_thresholds(c, exam_id, threshold)
            return self.get(exam_id, c)

    def delete(self, exam_id: str) -> list[str] | None:
        """Delete an exam and everything under it.

        scripts.exam_id has no ON DELETE CASCADE (it was added by a later migration), so
        scripts are removed first; that cascades to their extractions and evaluations. Jobs
        cascade from the exam row. Returns the deleted script ids (so their files can be
        removed), or None if the exam didn't exist.
        """
        with self.db.use() as c:
            if not self.get(exam_id, c):
                return None
            script_ids = [
                r["script_id"]
                for r in c.execute("SELECT script_id FROM scripts WHERE exam_id = ?", (exam_id,))
            ]
            c.execute("DELETE FROM scripts WHERE exam_id = ?", (exam_id,))
            c.execute("DELETE FROM exams WHERE exam_id = ?", (exam_id,))
            return script_ids

    # ── Scripts and results within an exam ───────────────────────────────────

    def scripts(self, exam_id: str, conn: Connection | None = None) -> list[dict[str, Any]]:
        with self.db.use(conn) as c:
            rows = c.execute(
                """
                SELECT sc.script_id, sc.student_id, sc.filename, sc.status, sc.page_count,
                       sc.error, sc.created_at, st.name AS student_name,
                       COUNT(qe.eval_id) AS graded_questions,
                       COALESCE(SUM(qe.awarded_marks), 0) AS total_awarded,
                       COALESCE(SUM(qe.max_marks), 0) AS total_max,
                       COALESCE(SUM(CASE WHEN qe.status != 'teacher_approved'
                                          AND qe.confidence < qe.threshold_used
                                         THEN 1 ELSE 0 END), 0) AS needs_review_count,
                       (SELECT COUNT(*) FROM question_extractions x
                        WHERE x.script_id = sc.script_id) AS extraction_count
                FROM scripts sc
                LEFT JOIN students st ON st.student_id = sc.student_id
                LEFT JOIN question_evaluations qe ON qe.script_id = sc.script_id
                WHERE sc.exam_id = ?
                GROUP BY sc.script_id, st.name
                ORDER BY st.name COLLATE NOCASE, sc.created_at
                """,
                (exam_id,),
            ).fetchall()
        scripts = []
        for r in rows:
            d = dict(r)
            # The stored status goes stale once the teacher approves flagged answers, so
            # derive it from the evaluations for anything that has been graded.
            if d["graded_questions"] > 0 and d["status"] not in ("processing", "error"):
                d["status"] = "needs_review" if d["needs_review_count"] > 0 else "graded"
            scripts.append(d)
        return scripts

    def results(self, exam: dict[str, Any]) -> dict[str, Any]:
        """Student x question grid with totals, ready for the results table and CSV."""
        questions = exam["questions"]
        question_ids = [q["question_id"] for q in questions]
        max_by_question = {q["question_id"]: q["max_marks"] for q in questions}
        total_max = round(sum(max_by_question.values()), 2)
        exam_threshold = exam.get("review_confidence_threshold", ReviewPolicy.DEFAULT_THRESHOLD)

        marks_by_script: dict[str, dict[str, dict[str, Any]]] = {}
        with self.db.use() as c:
            evaluation_rows = c.execute(
                """
                SELECT qe.script_id, qe.question_id, qe.awarded_marks, qe.max_marks,
                       qe.status, qe.confidence, qe.threshold_used
                FROM question_evaluations qe
                JOIN scripts s ON s.script_id = qe.script_id
                WHERE s.exam_id = ?
                """,
                (exam["exam_id"],),
            ).fetchall()
            script_rows = self.scripts(exam["exam_id"], c)

        for r in evaluation_rows:
            if r["question_id"] not in max_by_question:  # question removed from the key
                continue
            policy = ReviewPolicy(r["threshold_used"] or exam_threshold)
            marks_by_script.setdefault(r["script_id"], {})[r["question_id"]] = {
                "awarded": r["awarded_marks"],
                "max": r["max_marks"],
                "status": policy.effective_status(r["status"], r["confidence"]),
                "confidence": r["confidence"],
            }

        rows = []
        for s in script_rows:
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
                    "final": bool(question_ids)
                    and len(marks) == len(question_ids)
                    and pending == 0,
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

    def refresh_script_status(self, script_id: str) -> None:
        """Set a graded script's stored status from its evaluations."""
        with self.db.use() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN status != 'teacher_approved' "
                "AND confidence < threshold_used THEN 1 ELSE 0 END), 0) AS pending "
                "FROM question_evaluations WHERE script_id=?",
                (script_id,),
            ).fetchone()
            if row["n"]:
                status = "needs_review" if row["pending"] else "graded"
                c.execute("UPDATE scripts SET status=? WHERE script_id=?", (status, script_id))

    def threshold_for_script(self, script_id: str) -> float:
        """The review threshold of the exam a script belongs to."""
        with self.db.use() as c:
            row = c.execute(
                "SELECT e.review_confidence_threshold AS t FROM scripts s "
                "LEFT JOIN exams e ON e.exam_id = s.exam_id WHERE s.script_id = ?",
                (script_id,),
            ).fetchone()
        if row and row["t"] is not None:
            return ReviewPolicy.clamp(row["t"])
        return ReviewPolicy.DEFAULT_THRESHOLD


class EvaluatorConfigRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, config: dict[str, Any]) -> dict[str, Any]:
        config_id = uuid4().hex
        with self.db.use() as c:
            c.execute(
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
            return dict(
                c.execute(
                    "SELECT * FROM evaluator_configs WHERE evaluator_config_id=?", (config_id,)
                ).fetchone()
            )

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM evaluator_configs ORDER BY name COLLATE NOCASE"
                ).fetchall()
            ]

    def get(self, config_id: str) -> dict[str, Any] | None:
        with self.db.use() as c:
            row = c.execute(
                "SELECT * FROM evaluator_configs WHERE evaluator_config_id=?", (config_id,)
            ).fetchone()
            return dict(row) if row else None


class JobRepository:
    """Background work (processing / grading a whole exam) with progress."""

    _FIELDS = frozenset({"status", "total", "done", "message", "error"})

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _job(row: Any) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        d["cancel_requested"] = bool(d["cancel_requested"])
        return d

    def create(self, exam_id: str, kind: str, total: int) -> dict[str, Any]:
        job_id = uuid4().hex
        with self.db.use() as c:
            c.execute(
                "INSERT INTO jobs(job_id, exam_id, kind, total) VALUES (?, ?, ?, ?)",
                (job_id, exam_id, kind, total),
            )
            return self._job(c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())  # type: ignore[return-value]

    def active(self, exam_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        with self.db.use(conn) as c:
            return self._job(
                c.execute(
                    "SELECT * FROM jobs WHERE exam_id=? AND status IN ('queued', 'running') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (exam_id,),
                ).fetchone()
            )

    _LATEST_SQLITE = (
        "SELECT * FROM jobs WHERE exam_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1"
    )
    _LATEST_POSTGRES = (
        "SELECT * FROM jobs WHERE exam_id=? ORDER BY created_at DESC, job_id DESC LIMIT 1"
    )

    def latest(self, exam_id: str) -> dict[str, Any] | None:
        # SQLite timestamps only have one-second resolution, so insertion order (rowid) breaks
        # ties; PostgreSQL has no rowid but its timestamps have microseconds.
        query = self._LATEST_POSTGRES if self.db.is_postgres else self._LATEST_SQLITE
        with self.db.use() as c:
            return self._job(c.execute(query, (exam_id,)).fetchone())

    def update(self, job_id: str, **fields: Any) -> None:
        sets = {k: v for k, v in fields.items() if k in self._FIELDS}
        if not sets:
            return
        assignments = ", ".join(f"{k}=?" for k in sets)  # keys are whitelisted above
        with self.db.use() as c:
            c.execute(
                f"UPDATE jobs SET {assignments}, updated_at=datetime('now') WHERE job_id=?",  # noqa: S608
                (*sets.values(), job_id),
            )

    def request_cancel(self, exam_id: str) -> bool:
        with self.db.use() as c:
            cur = c.execute(
                "UPDATE jobs SET cancel_requested=1, updated_at=datetime('now') "
                "WHERE exam_id=? AND status IN ('queued', 'running')",
                (exam_id,),
            )
            return bool(cur.rowcount > 0)

    def is_cancel_requested(self, job_id: str) -> bool:
        with self.db.use() as c:
            row = c.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def mark_interrupted(self) -> None:
        """Jobs left 'running' by a server restart can never finish; close them out."""
        with self.db.use() as c:
            c.execute(
                "UPDATE jobs SET status='failed', error='Interrupted by a server restart', "
                "updated_at=datetime('now') WHERE status IN ('queued', 'running')"
            )
