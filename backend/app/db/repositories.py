"""Repository classes: all SQL for students, scripts, extractions, evaluations and rubric
templates. Each takes a ``Database``; every method accepts an optional open connection so
several calls can share one transaction."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from backend.app.db.database import Connection, Database
from backend.app.models.domain import GradeResult, QuestionSpec, ReviewPolicy


def _rows(cursor: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


class StudentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, student_id: str, name: str = "", conn: Connection | None = None) -> None:
        with self.db.use(conn) as c:
            c.execute(
                "INSERT OR IGNORE INTO students(student_id, name) VALUES (?, ?)",
                (student_id, name),
            )

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return _rows(
                c.execute(
                    "SELECT s.student_id, s.name, s.created_at, "
                    "COUNT(sc.script_id) AS script_count "
                    "FROM students s LEFT JOIN scripts sc ON s.student_id=sc.student_id "
                    "GROUP BY s.student_id ORDER BY s.created_at DESC"
                )
            )

    def get(self, student_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        with self.db.use(conn) as c:
            row = c.execute("SELECT * FROM students WHERE student_id=?", (student_id,)).fetchone()
            return dict(row) if row else None


class ScriptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(
        self,
        script_id: str,
        student_id: str,
        filename: str,
        file_path: str,
        exam_id: str | None = None,
    ) -> None:
        with self.db.use() as c:
            c.execute(
                "INSERT OR IGNORE INTO scripts "
                "(script_id, student_id, filename, file_path, exam_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (script_id, student_id, filename, file_path, exam_id),
            )

    def get(self, script_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        with self.db.use(conn) as c:
            row = c.execute("SELECT * FROM scripts WHERE script_id=?", (script_id,)).fetchone()
            return dict(row) if row else None

    def list_for_student(self, student_id: str) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return _rows(
                c.execute(
                    "SELECT * FROM scripts WHERE student_id=? ORDER BY created_at DESC",
                    (student_id,),
                )
            )

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return _rows(
                c.execute(
                    "SELECT sc.*, st.name AS student_name, e.name AS exam_name FROM scripts sc "
                    "LEFT JOIN students st ON sc.student_id=st.student_id "
                    "LEFT JOIN exams e ON sc.exam_id=e.exam_id "
                    "ORDER BY sc.created_at DESC"
                )
            )

    def set_status(
        self,
        script_id: str,
        status: str,
        page_count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Set the status and clear/set the error. page_count is only changed when given."""
        with self.db.use() as c:
            c.execute(
                "UPDATE scripts SET status=?, page_count=COALESCE(?, page_count), error=? "
                "WHERE script_id=?",
                (status, page_count, error, script_id),
            )

    def reset_stuck(self, script_ids: list[str]) -> None:
        """Put scripts a cancelled/failed job left 'processing' back to 'uploaded'."""
        with self.db.use() as c:
            c.executemany(
                "UPDATE scripts SET status='uploaded' WHERE script_id=? AND status='processing'",
                [(sid,) for sid in script_ids],
            )

    def label(self, script_id: str) -> str:
        with self.db.use() as c:
            row = c.execute(
                "SELECT sc.filename, st.name FROM scripts sc "
                "LEFT JOIN students st ON st.student_id = sc.student_id WHERE sc.script_id=?",
                (script_id,),
            ).fetchone()
        return (row["name"] or row["filename"]) if row else script_id

    def delete(self, script_id: str) -> None:
        with self.db.use() as c:
            c.execute("DELETE FROM scripts WHERE script_id=?", (script_id,))


class ExtractionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        script_id: str,
        question_id: str,
        extracted_text: str,
        extraction_method: str,
        *,
        page_number: int | None = None,
        question_number: int | None = None,
        question_type: str = "unknown",
        extraction_confidence: float | None = None,
        needs_review: bool = False,
        review_reason: str = "",
    ) -> None:
        with self.db.use() as c:
            c.execute(
                """
                INSERT INTO question_extractions
                    (extraction_id, script_id, question_id, question_number, question_type,
                     page_number, extracted_text, extraction_method, extraction_confidence,
                     needs_review, review_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(script_id, question_id) DO UPDATE SET
                    question_number=excluded.question_number,
                    question_type=excluded.question_type,
                    page_number=excluded.page_number,
                    extracted_text=excluded.extracted_text,
                    extraction_method=excluded.extraction_method,
                    extraction_confidence=excluded.extraction_confidence,
                    needs_review=excluded.needs_review,
                    review_reason=excluded.review_reason
                """,
                (
                    uuid4().hex,
                    script_id,
                    question_id,
                    question_number,
                    question_type,
                    page_number,
                    extracted_text,
                    extraction_method,
                    extraction_confidence,
                    int(needs_review),
                    review_reason,
                ),
            )

    def for_script(self, script_id: str, conn: Connection | None = None) -> list[dict[str, Any]]:
        with self.db.use(conn) as c:
            return _rows(
                c.execute(
                    "SELECT * FROM question_extractions WHERE script_id=? ORDER BY question_id",
                    (script_id,),
                )
            )

    def answers_for_script(self, script_id: str) -> tuple[dict[str, str], dict[str, int]]:
        """(question_id -> extracted text, question_id -> page number)."""
        rows = self.for_script(script_id)
        answers = {r["question_id"]: r["extracted_text"] for r in rows}
        pages = {r["question_id"]: r["page_number"] for r in rows if r["page_number"] is not None}
        return answers, pages


class EvaluationRepository:
    """Per-question grades. Whether a grade "needs review" is derived at read time from its
    stored confidence and threshold (see ReviewPolicy)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        script_id: str,
        spec: QuestionSpec,
        result: GradeResult,
        threshold: float,
        conn: Connection | None = None,
    ) -> None:
        # A teacher's decision is final: the WHERE clause below makes a re-grade (from any path:
        # jobs, the per-script endpoint, a batch run) leave an approved answer untouched. The
        # columns written at grade time that depend on the threshold are ignored when reading;
        # `status` only matters here for 'teacher_approved'.
        llm_reasoning = dict(result.llm_reasoning or {})
        llm_reasoning["flag_reasons"] = result.flag_reasons
        with self.db.use(conn) as c:
            c.execute(
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
                WHERE question_evaluations.status != 'teacher_approved'
                """,
                (
                    uuid4().hex,
                    script_id,
                    spec.question_id,
                    spec.question_text,
                    result.question_type or spec.question_type or "unknown",
                    result.answer_text,
                    result.page_number,
                    spec.golden_answer,
                    json.dumps(spec.criteria),
                    spec.max_marks,
                    result.awarded_marks,
                    result.confidence,
                    threshold,
                    json.dumps(result.criteria_scores),
                    result.status,
                    json.dumps(result.evidence),
                    result.reasoning,
                    json.dumps(llm_reasoning),
                    result.llm_model,
                    json.dumps(result.evaluator_snapshot) if result.evaluator_snapshot else None,
                ),
            )

    def delete_ai_grades(self, keys: list[tuple[str, str]]) -> None:
        """Delete the stored grades for (script_id, question_id) pairs ahead of a re-grade.
        A teacher-approved grade is never deleted."""
        if not keys:
            return
        with self.db.use() as c:
            c.executemany(
                "DELETE FROM question_evaluations "
                "WHERE script_id=? AND question_id=? AND status != 'teacher_approved'",
                keys,
            )

    def apply_override(
        self, script_id: str, question_id: str, awarded_marks: float, reason: str
    ) -> bool:
        with self.db.use() as c:
            cursor = c.execute(
                """
                UPDATE question_evaluations
                SET awarded_marks=?, teacher_override=?, teacher_override_reason=?,
                    status='teacher_approved', updated_at=datetime('now')
                WHERE script_id=? AND question_id=?
                """,
                (awarded_marks, awarded_marks, reason, script_id, question_id),
            )
            return bool(cursor.rowcount > 0)

    def statuses_for_exam(self, exam_id: str) -> dict[tuple[str, str], str]:
        with self.db.use() as c:
            return {
                (r["script_id"], r["question_id"]): r["status"]
                for r in c.execute(
                    "SELECT qe.script_id, qe.question_id, qe.status FROM question_evaluations qe "
                    "JOIN scripts s ON s.script_id = qe.script_id WHERE s.exam_id=?",
                    (exam_id,),
                )
            }

    def for_script(self, script_id: str, conn: Connection | None = None) -> list[dict[str, Any]]:
        with self.db.use(conn) as c:
            rows = c.execute(
                "SELECT * FROM question_evaluations WHERE script_id=? ORDER BY question_id",
                (script_id,),
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["rubric"] = json.loads(d.pop("rubric_json", "[]"))
            d["evidence"] = json.loads(d.pop("evidence_json", "[]"))
            d["criteria_scores"] = json.loads(d.pop("criteria_scores_json", "[]"))
            snapshot_raw = d.pop("evaluator_snapshot_json", None)
            d["evaluator_snapshot"] = json.loads(snapshot_raw) if snapshot_raw else None
            llm_raw = d.pop("llm_reasoning_json", None)
            d["llm_reasoning"] = json.loads(llm_raw) if llm_raw else None

            policy = ReviewPolicy(d.get("threshold_used"))
            d["status"] = policy.effective_status(d["status"], d["confidence"])
            d["needs_review"] = d["status"] == "needs_review"
            d["review_threshold"] = policy.threshold
            stored = (d["llm_reasoning"] or {}).get("flag_reasons")
            reasons = policy.flag_reasons(stored, d["confidence"], d["status"])
            if isinstance(d["llm_reasoning"], dict):
                d["llm_reasoning"]["flag_reasons"] = reasons
            d["flag_reasons"] = reasons
            results.append(d)
        return results

    def totals_for_script(self, script_id: str) -> dict[str, Any]:
        with self.db.use() as c:
            row = c.execute(
                """
                SELECT
                    COUNT(*) as total_questions,
                    COALESCE(SUM(awarded_marks), 0) as total_awarded,
                    COALESCE(SUM(max_marks), 0) as total_max,
                    COALESCE(SUM(CASE WHEN status != 'teacher_approved'
                                       AND confidence < threshold_used
                                      THEN 1 ELSE 0 END), 0) as needs_review_count,
                    COALESCE(SUM(CASE WHEN status='teacher_approved' THEN 1 ELSE 0 END), 0)
                        as approved_count
                FROM question_evaluations WHERE script_id=?
                """,
                (script_id,),
            ).fetchone()
            return dict(row) if row else {}

    def review_queue(self, exam_id: str | None = None) -> list[dict[str, Any]]:
        with self.db.use() as c:
            rows = c.execute(
                """
                SELECT qe.eval_id, qe.script_id, qe.question_id, qe.question_text,
                    qe.page_number, qe.golden_answer, qe.max_marks, qe.awarded_marks,
                    qe.confidence, qe.status, qe.reasoning, qe.llm_model,
                    qe.evidence_json, qe.llm_reasoning_json, qe.created_at,
                    s.filename, s.student_id, s.page_count, st.name AS student_name,
                    qe_source.source_path, qe_source.extracted_text AS answer_text,
                    qe.threshold_used AS review_threshold
                FROM question_evaluations qe
                JOIN scripts s ON qe.script_id = s.script_id
                LEFT JOIN students st ON s.student_id = st.student_id
                LEFT JOIN question_extractions qe_source
                    ON qe.script_id = qe_source.script_id AND qe.question_id = qe_source.question_id
                WHERE qe.status != 'teacher_approved'
                  AND qe.confidence < qe.threshold_used
                  AND (CAST(? AS TEXT) IS NULL OR s.exam_id = ?)
                ORDER BY s.filename, qe.question_id
                """,
                (exam_id, exam_id),
            ).fetchall()

        items = []
        for row in rows:
            item = dict(row)
            try:
                item["evidence"] = json.loads(item.pop("evidence_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                item["evidence"] = []
            try:
                llm = json.loads(item.pop("llm_reasoning_json", None) or "{}")
                stored = llm.get("flag_reasons", []) if isinstance(llm, dict) else []
            except (json.JSONDecodeError, TypeError):
                stored = []
            policy = ReviewPolicy(item["review_threshold"])
            item["review_threshold"] = policy.threshold
            item["status"] = "needs_review"
            item["flag_reasons"] = policy.flag_reasons(stored, item["confidence"], item["status"])
            items.append(item)
        return items

    def stats(self) -> dict[str, int]:
        def count(conn: Connection, where: str = "", table: str = "question_evaluations") -> int:
            query = f"SELECT COUNT(*) AS n FROM {table}{where}"  # noqa: S608 - fixed fragments
            return int(conn.execute(query).fetchone()["n"])

        with self.db.use() as c:
            total_students = count(c, table="students")
            total_scripts = count(c, table="scripts")
            graded = count(c)
            needs_review = count(
                c, " WHERE status != 'teacher_approved' AND confidence < threshold_used"
            )
            approved = count(c, " WHERE status='teacher_approved'")
        return {
            "total_students": total_students,
            "total_scripts": total_scripts,
            "total_questions_graded": graded,
            "needs_review": needs_review,
            "approved": approved,
            "scored": graded - approved - needs_review,
        }


class RubricConfigRepository:
    """Reusable rubric templates."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return _rows(c.execute("SELECT * FROM rubric_configs ORDER BY created_at DESC"))

    def save(self, name: str, config: Any) -> str:
        config_id = uuid4().hex
        with self.db.use() as c:
            c.execute(
                "INSERT INTO rubric_configs(config_id, name, config_json) VALUES (?, ?, ?)",
                (config_id, name, json.dumps(config)),
            )
        return config_id

    def get(self, config_id: str) -> dict[str, Any] | None:
        with self.db.use() as c:
            row = c.execute(
                "SELECT * FROM rubric_configs WHERE config_id=?", (config_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json", "{}"))
        return result
