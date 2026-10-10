"""Exam-level persistence: exams and their answer key, evaluator configs, per-exam summaries
and results, and background jobs.

The answer key lives in exam_questions / question_options / rubric_criteria and nowhere else.
Saving a key upserts questions by (exam_id, question_id), so a question keeps its row id (and
the grades pointing at it) across edits; questions dropped from the key are deleted, which
removes their grades too.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from backend.app.db.database import Connection, Database
from backend.app.db.repositories import as_uuid, returned
from backend.app.models.domain import ReviewPolicy


class JobConflict(RuntimeError):
    """A job is already queued or running for this exam."""


_SUMMARY_SQL = """
    select e.id as exam_id, e.name, e.review_threshold as review_confidence_threshold,
           e.mark_step, e.created_at, e.updated_at,
           (select count(*) from scripts s where s.exam_id = e.id)::int as script_count,
           (select count(*) from scripts s
             where s.exam_id = e.id and s.status = 'processed')::int as processed_count,
           (select count(*) from scripts s
             where s.exam_id = e.id and s.status = 'error')::int as error_count,
           (select count(distinct ev.script_id) from evaluations ev
              join scripts s on s.id = ev.script_id
             where s.exam_id = e.id)::int as graded_count,
           (select count(*) from evaluation_state es
              join scripts s on s.id = es.script_id
             where s.exam_id = e.id and es.needs_review)::int as review_count,
           (select count(*) from exam_questions q where q.exam_id = e.id)::int as question_count,
           (select coalesce(sum(q.max_marks), 0) from exam_questions q
             where q.exam_id = e.id) as total_marks,
           (exists (select 1 from exam_questions q where q.exam_id = e.id)
            and not exists (select 1 from exam_questions q
                             where q.exam_id = e.id and btrim(q.golden_answer) = ''))
               as answer_key_complete
    from exams e
"""


def _optional_uuid(value: Any, what: str) -> UUID | None:
    if value in (None, ""):
        return None
    parsed = as_uuid(value)
    if parsed is None:
        raise ValueError(f"{what} is not a valid id: {value!r}")
    return parsed


class ExamRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Exams ────────────────────────────────────────────────────────────────

    @staticmethod
    def _questions(conn: Connection, exam_id: UUID) -> list[dict[str, Any]]:
        questions = conn.execute(
            """
            select q.id as row_id, q.question_id, q.question_number, q.question_text,
                   q.question_type::text as question_type, q.golden_answer, q.max_marks,
                   q.review_threshold as review_confidence_threshold, q.evaluator_config_id
            from exam_questions q where q.exam_id = %s order by q.question_number
            """,
            (exam_id,),
        ).fetchall()
        criteria: dict[str, list[dict[str, Any]]] = {}
        for c in conn.execute(
            """
            select rc.exam_question_id, rc.name, rc.description, rc.max_marks as marks,
                   rc.expected_concepts, rc.guidance
            from rubric_criteria rc join exam_questions q on q.id = rc.exam_question_id
            where q.exam_id = %s order by rc.display_order
            """,
            (exam_id,),
        ):
            criteria.setdefault(c.pop("exam_question_id"), []).append(c)
        options: dict[str, list[dict[str, Any]]] = {}
        for o in conn.execute(
            """
            select qo.exam_question_id, qo.option_key, qo.option_text, qo.is_correct,
                   qo.display_order
            from question_options qo join exam_questions q on q.id = qo.exam_question_id
            where q.exam_id = %s order by qo.display_order
            """,
            (exam_id,),
        ):
            options.setdefault(o.pop("exam_question_id"), []).append(o)
        for question in questions:
            row_id = question.pop("row_id")
            question["criteria"] = criteria.get(row_id, [])
            question["options"] = options.get(row_id, [])
        return list(questions)

    def _summarize(
        self, row: dict[str, Any], conn: Connection, include_questions: bool
    ) -> dict[str, Any]:
        row["review_confidence_threshold"] = ReviewPolicy.clamp(row["review_confidence_threshold"])
        row["total_marks"] = round(row["total_marks"], 2)
        if include_questions:
            row["questions"] = self._questions(conn, UUID(row["exam_id"]))
        return row

    def create(self, name: str) -> dict[str, Any]:
        with self.db.use() as c:
            created = returned(
                c.execute(
                    "insert into exams (name) values (%s) returning id", (name.strip(),)
                ).fetchone()
            )
            return returned(self.get(created["id"], c))

    def get(self, exam_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return None
        with self.db.use(conn) as c:
            row = c.execute(_SUMMARY_SQL + " where e.id = %s", (exam_uuid,)).fetchone()
            return self._summarize(row, c, include_questions=True) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            rows = c.execute(_SUMMARY_SQL + " order by e.created_at desc").fetchall()
            return [self._summarize(r, c, include_questions=False) for r in rows]

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        with self.db.use() as c:
            row = c.execute(
                "select id from exams where name = %s order by created_at limit 1", (name,)
            ).fetchone()
            return self.get(row["id"], c) if row else None

    @staticmethod
    def _save_questions(conn: Connection, exam_id: UUID, questions: list[dict[str, Any]]) -> None:
        keep: list[str] = []
        for number, question in enumerate(questions, start=1):
            keep.append(question["question_id"])
            saved = returned(
                conn.execute(
                    """
                insert into exam_questions
                    (exam_id, question_id, question_number, question_type, question_text,
                     golden_answer, max_marks, review_threshold, evaluator_config_id)
                values (%s, %s, %s, %s::question_type, %s, %s, %s, %s, %s)
                on conflict (exam_id, question_id) do update set
                    question_number     = excluded.question_number,
                    question_type       = excluded.question_type,
                    question_text       = excluded.question_text,
                    golden_answer       = excluded.golden_answer,
                    max_marks           = excluded.max_marks,
                    review_threshold    = excluded.review_threshold,
                    evaluator_config_id = excluded.evaluator_config_id
                returning id
                """,
                    (
                        exam_id,
                        question["question_id"],
                        number,
                        question["question_type"],
                        question.get("question_text", ""),
                        question.get("golden_answer", ""),
                        question["max_marks"],
                        question.get("review_confidence_threshold"),
                        _optional_uuid(question.get("evaluator_config_id"), "evaluator_config_id"),
                    ),
                ).fetchone()
            )
            row_id = saved["id"]
            conn.execute("delete from rubric_criteria where exam_question_id = %s", (row_id,))
            conn.execute("delete from question_options where exam_question_id = %s", (row_id,))
            for index, criterion in enumerate(question.get("criteria", [])):
                conn.execute(
                    """
                    insert into rubric_criteria
                        (exam_question_id, name, description, max_marks, expected_concepts,
                         guidance, display_order)
                    values (%s, %s, %s, %s, %s::text[], %s, %s)
                    """,
                    (
                        row_id,
                        criterion["name"],
                        criterion.get("description", ""),
                        criterion["marks"],
                        criterion.get("expected_concepts", []),
                        criterion.get("guidance", ""),
                        index,
                    ),
                )
            for index, option in enumerate(question.get("options", [])):
                conn.execute(
                    """
                    insert into question_options
                        (exam_question_id, option_key, option_text, is_correct, display_order)
                    values (%s, %s, %s, %s, %s)
                    """,
                    (
                        row_id,
                        option["option_key"],
                        option["option_text"],
                        bool(option.get("is_correct", False)),
                        option.get("display_order", index),
                    ),
                )
        conn.execute(
            "delete from exam_questions where exam_id = %s and question_id <> all(%s::text[])",
            (exam_id, keep),
        )

    def update(
        self,
        exam_id: str,
        name: str | None = None,
        questions: list[dict[str, Any]] | None = None,
        review_confidence_threshold: float | None = None,
        mark_step: float | None = None,
    ) -> dict[str, Any] | None:
        """Rename an exam, change its review threshold or mark step, and/or replace its answer
        key. Raises ValueError for an unknown evaluator config."""
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return None
        try:
            with self.db.use() as c:
                exam = self.get(exam_id, c)
                if not exam:
                    return None
                threshold = (
                    ReviewPolicy.clamp(review_confidence_threshold)
                    if review_confidence_threshold is not None
                    else exam["review_confidence_threshold"]
                )
                c.execute(
                    "update exams set name = %s, review_threshold = %s, mark_step = %s "
                    "where id = %s",
                    (
                        name.strip() if name is not None else exam["name"],
                        threshold,
                        mark_step if mark_step is not None else exam["mark_step"],
                        exam_uuid,
                    ),
                )
                if questions is not None:
                    self._save_questions(c, exam_uuid, questions)
                return self.get(exam_id, c)
        except psycopg.errors.ForeignKeyViolation as exc:
            raise ValueError(
                "A question refers to an evaluator config that does not exist"
            ) from exc

    def delete(self, exam_id: str) -> list[str] | None:
        """Delete an exam and everything under it (scripts, extractions, grades, jobs cascade).
        Returns the deleted script ids so their files can be removed, or None if the exam
        didn't exist."""
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return None
        with self.db.use() as c:
            if c.execute("select 1 from exams where id = %s", (exam_uuid,)).fetchone() is None:
                return None
            script_ids = [
                r["id"]
                for r in c.execute("select id from scripts where exam_id = %s", (exam_uuid,))
            ]
            c.execute("delete from exams where id = %s", (exam_uuid,))
            return script_ids

    # ── Scripts and results within an exam ───────────────────────────────────

    def scripts(self, exam_id: str, conn: Connection | None = None) -> list[dict[str, Any]]:
        """Every script in the exam with its pipeline/grading status and totals. The status is
        derived by the script_summary view: graded and needs-review are never stored, so they
        cannot go stale when a teacher approves answers or a threshold changes."""
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return []
        with self.db.use(conn) as c:
            return list(
                c.execute(
                    """
                    select ss.script_id, st.external_id as student_id, ss.filename, ss.status,
                           ss.page_count, ss.error, ss.created_at, st.name as student_name,
                           ss.graded_questions, ss.total_awarded, ss.total_max,
                           ss.needs_review_count,
                           (select count(*) from extractions x
                             where x.script_id = ss.script_id)::int as extraction_count
                    from script_summary ss join students st on st.id = ss.student_id
                    where ss.exam_id = %s
                    order by lower(st.name), ss.created_at
                    """,
                    (exam_uuid,),
                ).fetchall()
            )

    def results(self, exam: dict[str, Any]) -> dict[str, Any]:
        """Student x question grid with totals, ready for the results table and CSV."""
        questions = exam["questions"]
        question_ids = [q["question_id"] for q in questions]
        max_by_question = {q["question_id"]: q["max_marks"] for q in questions}
        total_max = round(sum(max_by_question.values()), 2)

        marks_by_script: dict[str, dict[str, dict[str, Any]]] = {}
        with self.db.use() as c:
            evaluation_rows = c.execute(
                """
                select es.script_id, q.question_id, es.final_marks as awarded,
                       es.max_marks as max, es.confidence,
                       case when es.review_status = 'teacher_approved' then 'teacher_approved'
                            when es.needs_review then 'needs_review'
                            else 'scored' end as status
                from evaluation_state es
                join scripts s on s.id = es.script_id
                join exam_questions q on q.id = es.exam_question_id
                where s.exam_id = %s
                """,
                (UUID(exam["exam_id"]),),
            ).fetchall()
            script_rows = self.scripts(exam["exam_id"], c)

        for r in evaluation_rows:
            marks_by_script.setdefault(r["script_id"], {})[r["question_id"]] = {
                "awarded": r["awarded"],
                "max": r["max"],
                "status": r["status"],
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


_EVALUATOR_COLUMNS = (
    "id as evaluator_config_id, name, kind::text as evaluator_kind, provider_name, model_name, "
    "temperature, max_tokens, fallback_config_id, created_at, updated_at"
)


class EvaluatorConfigRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, config: dict[str, Any]) -> dict[str, Any]:
        """Raises ValueError when fallback_config_id isn't a real config."""
        try:
            with self.db.use() as c:
                row = c.execute(
                    f"""
                    insert into evaluator_configs
                        (name, kind, provider_name, model_name, temperature, max_tokens,
                         fallback_config_id)
                    values (%s, %s::grader_kind, %s, %s, %s, %s, %s)
                    returning {_EVALUATOR_COLUMNS}
                    """,  # noqa: S608 - the column list is a constant
                    (
                        config["name"].strip(),
                        config["evaluator_kind"],
                        config["provider_name"].strip(),
                        config["model_name"].strip(),
                        config.get("temperature", 0.0),
                        config.get("max_tokens"),
                        _optional_uuid(config.get("fallback_config_id"), "fallback_config_id"),
                    ),
                ).fetchone()
        except psycopg.errors.ForeignKeyViolation as exc:
            raise ValueError("fallback_config_id does not match an existing config") from exc
        return returned(row)

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return list(
                c.execute(
                    f"select {_EVALUATOR_COLUMNS} from evaluator_configs order by lower(name)"  # noqa: S608
                ).fetchall()
            )

    def get(self, config_id: str) -> dict[str, Any] | None:
        config_uuid = as_uuid(config_id)
        if config_uuid is None:
            return None
        with self.db.use() as c:
            return c.execute(
                f"select {_EVALUATOR_COLUMNS} from evaluator_configs where id = %s",  # noqa: S608
                (config_uuid,),
            ).fetchone()


_JOB_COLUMNS = (
    "id as job_id, exam_id, kind::text as kind, status::text as status, total, done, message, "
    "error, cancel_requested, heartbeat_at, started_at, finished_at, created_at, updated_at"
)


class JobRepository:
    """Background work (processing / grading a whole exam) with progress. The database allows
    at most one queued-or-running job per exam, so a second start raises JobConflict even when
    two requests race."""

    _FIELDS = frozenset({"status", "total", "done", "message", "error"})

    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, exam_id: str, kind: str, total: int) -> dict[str, Any]:
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            raise LookupError("Exam not found")
        try:
            with self.db.use() as c:
                row = c.execute(
                    f"insert into jobs (exam_id, kind, total) "  # noqa: S608
                    f"values (%s, %s::job_kind, %s) returning {_JOB_COLUMNS}",
                    (exam_uuid, kind, total),
                ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise JobConflict("Another job is already running for this exam.") from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise LookupError("Exam not found") from exc
        return returned(row)

    def active(self, exam_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return None
        with self.db.use(conn) as c:
            return c.execute(
                f"select {_JOB_COLUMNS} from jobs "  # noqa: S608
                "where exam_id = %s and status in ('queued', 'running') limit 1",
                (exam_uuid,),
            ).fetchone()

    def latest(self, exam_id: str) -> dict[str, Any] | None:
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return None
        with self.db.use() as c:
            return c.execute(
                f"select {_JOB_COLUMNS} from jobs where exam_id = %s "  # noqa: S608
                "order by created_at desc, id desc limit 1",
                (exam_uuid,),
            ).fetchone()

    def update(self, job_id: str, **fields: Any) -> None:
        """Update progress. Every call is also a heartbeat; entering 'running' stamps
        started_at and a terminal status stamps finished_at."""
        job_uuid = as_uuid(job_id)
        sets = {k: v for k, v in fields.items() if k in self._FIELDS}
        if job_uuid is None or not sets:
            return
        assignments = [  # keys are whitelisted above
            f"{key} = %s::job_status" if key == "status" else f"{key} = %s" for key in sets
        ]
        assignments.append("heartbeat_at = now()")
        status = sets.get("status")
        if status == "running":
            assignments.append("started_at = coalesce(started_at, now())")
        elif status in ("done", "failed", "cancelled"):
            assignments.append("finished_at = now()")
        with self.db.use() as c:
            c.execute(
                f"update jobs set {', '.join(assignments)} where id = %s",  # noqa: S608
                (*sets.values(), job_uuid),
            )

    def request_cancel(self, exam_id: str) -> bool:
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return False
        with self.db.use() as c:
            cursor = c.execute(
                "update jobs set cancel_requested = true "
                "where exam_id = %s and status in ('queued', 'running')",
                (exam_uuid,),
            )
            return bool(cursor.rowcount > 0)

    def is_cancel_requested(self, job_id: str) -> bool:
        job_uuid = as_uuid(job_id)
        if job_uuid is None:
            return False
        with self.db.use() as c:
            row = c.execute(
                "select cancel_requested from jobs where id = %s", (job_uuid,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def mark_interrupted(self) -> None:
        """Jobs left 'running' by a server restart can never finish; close them out."""
        with self.db.use() as c:
            c.execute(
                "update jobs set status = 'failed', error = 'Interrupted by a server restart', "
                "finished_at = now() where status in ('queued', 'running')"
            )
