"""Repository classes: all SQL for students, scripts, extractions and evaluations.

Each takes a ``Database``; every method accepts an optional open connection so several calls
can share one transaction. Rows come back as plain dicts whose ids are strings and whose
numeric columns are floats (see Database).

Two ideas to keep in mind when reading the evaluation queries:

* ``ai_marks`` is what the model awarded and is never changed after grading. ``final_marks`` is
  what counts: it equals ``ai_marks`` until a teacher overrides it. The API still calls it
  ``awarded_marks``.
* "Needs review" is never stored. The ``evaluation_state`` view derives it from the confidence,
  the threshold currently configured for the question/exam, and any rule-based flags, so
  changing a threshold never needs a re-grade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from backend.app.db.database import Connection, Database
from backend.app.models.domain import GradeResult, QuestionSpec, ReviewPolicy
from psycopg.types.json import Jsonb


class ScriptConflict(ValueError):
    """The exam already has a script for this student, or this exact file."""


def as_uuid(value: Any) -> UUID | None:
    """Parse an id coming from a URL or request body; None when it isn't a UUID, so callers can
    answer "not found" instead of crashing the query."""
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def returned(row: dict[str, Any] | None) -> dict[str, Any]:
    """The row an ``INSERT ... RETURNING`` or an aggregate ``SELECT`` always yields."""
    if row is None:
        raise RuntimeError("The database returned no row")
    return row


class StudentRepository:
    """Students are addressed by their external id (roll number, dataset id) everywhere in the
    API; the UUID primary key stays internal."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, student_id: str, name: str = "", conn: Connection | None = None) -> None:
        with self.db.use(conn) as c:
            c.execute(
                "insert into students (external_id, name) values (%s, %s) "
                "on conflict (external_id) do nothing",
                (student_id, name),
            )

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            return list(
                c.execute(
                    "select st.external_id as student_id, st.name, st.created_at, "
                    "count(sc.id)::int as script_count "
                    "from students st left join scripts sc on sc.student_id = st.id "
                    "group by st.id order by st.created_at desc"
                ).fetchall()
            )

    def get(self, student_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        with self.db.use(conn) as c:
            return c.execute(
                "select external_id as student_id, name, created_at "
                "from students where external_id = %s",
                (student_id,),
            ).fetchone()


_SCRIPT_SELECT = """
    select sc.id as script_id, st.external_id as student_id, st.name as student_name,
           sc.exam_id, e.name as exam_name, sc.filename, sc.storage_key,
           sc.status::text as status, sc.page_count, sc.error, sc.created_at
    from scripts sc
    join students st on st.id = sc.student_id
    join exams e on e.id = sc.exam_id
"""


class ScriptRepository:
    """Uploaded scripts. The row stores an object key; the local processing copy lives at
    ``upload_dir/<script_id><suffix>``, which is what ``file_path`` points at."""

    def __init__(self, db: Database, upload_dir: Path) -> None:
        self.db = db
        self.upload_dir = upload_dir

    def _script(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        suffix = Path(row["storage_key"]).suffix
        row["file_path"] = str(self.upload_dir / f"{row['script_id']}{suffix}")
        return row

    @staticmethod
    def object_key(script_id: str, file_path: str) -> str:
        return f"uploads/{script_id}/original{Path(file_path).suffix.lower()}"

    def insert(
        self,
        script_id: str,
        student_id: str,
        filename: str,
        file_path: str,
        exam_id: str,
        file_sha256: str | None = None,
        student_name: str | None = None,
    ) -> None:
        """Add a script to an exam. Raises ScriptConflict for a second script by the same
        student or a byte-identical file, and LookupError for an unknown exam or student.

        Pass ``student_name`` to create the student in the same transaction, so a rejected
        upload leaves no student behind."""
        exam_uuid, script_uuid = as_uuid(exam_id), as_uuid(script_id)
        if exam_uuid is None:
            raise LookupError("Exam not found")
        if script_uuid is None:
            raise ValueError("script_id must be a UUID")
        try:
            with self.db.use() as c:
                if student_name is not None:
                    StudentRepository(self.db).upsert(student_id, student_name, c)
                cursor = c.execute(
                    "insert into scripts (id, exam_id, student_id, filename, storage_key, "
                    "file_sha256) "
                    "select %s, %s, st.id, %s, %s, %s from students st where st.external_id = %s",
                    (
                        script_uuid,
                        exam_uuid,
                        filename,
                        self.object_key(str(script_uuid), file_path),
                        file_sha256,
                        student_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise LookupError("Student not found")
        except psycopg.errors.ForeignKeyViolation as exc:
            raise LookupError("Exam not found") from exc
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "scripts_exam_sha_uniq":
                raise ScriptConflict("This exact file is already uploaded to this exam.") from exc
            raise ScriptConflict("This student already has a script in this exam.") from exc

    def get(self, script_id: str, conn: Connection | None = None) -> dict[str, Any] | None:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return None
        with self.db.use(conn) as c:
            return self._script(
                c.execute(_SCRIPT_SELECT + " where sc.id = %s", (script_uuid,)).fetchone()
            )

    def list_for_student(self, student_id: str) -> list[dict[str, Any]]:
        with self.db.use() as c:
            rows = c.execute(
                _SCRIPT_SELECT + " where st.external_id = %s order by sc.created_at desc",
                (student_id,),
            ).fetchall()
        return [s for s in (self._script(r) for r in rows) if s]

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.use() as c:
            rows = c.execute(_SCRIPT_SELECT + " order by sc.created_at desc").fetchall()
        return [s for s in (self._script(r) for r in rows) if s]

    def set_status(
        self,
        script_id: str,
        status: str,
        page_count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Set the pipeline status (uploaded / processing / processed / error) and set or
        clear the error. page_count is only changed when given. Whether a script is graded or
        needs review is derived from its evaluations, never stored here."""
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return
        with self.db.use() as c:
            c.execute(
                "update scripts set status = %s::script_status, "
                "page_count = coalesce(%s, page_count), error = %s where id = %s",
                (status, page_count, error, script_uuid),
            )

    def set_page_count(self, script_id: str, page_count: int) -> None:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return
        with self.db.use() as c:
            c.execute("update scripts set page_count = %s where id = %s", (page_count, script_uuid))

    def reset_stuck(self, script_ids: list[str]) -> None:
        """Put scripts a cancelled/failed job left 'processing' back to 'uploaded'."""
        ids = [u for u in (as_uuid(s) for s in script_ids) if u is not None]
        if not ids:
            return
        with self.db.use() as c:
            c.execute(
                "update scripts set status = 'uploaded' "
                "where id = any(%s::uuid[]) and status = 'processing'",
                (ids,),
            )

    def label(self, script_id: str) -> str:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return script_id
        with self.db.use() as c:
            row = c.execute(
                "select coalesce(nullif(st.name, ''), sc.filename) as label "
                "from scripts sc join students st on st.id = sc.student_id where sc.id = %s",
                (script_uuid,),
            ).fetchone()
        return str(row["label"]) if row else script_id

    def delete(self, script_id: str) -> None:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return
        with self.db.use() as c:
            c.execute("delete from scripts where id = %s", (script_uuid,))


class ExtractionRepository:
    """What the vision model read from each script, one row per (script, question label)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        script_id: str,
        question_id: str,
        extracted_text: str,
        *,
        page_numbers: list[int] | None = None,
        question_number: int | None = None,
        confidence: float | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        flags: list[str] | None = None,
        answer_state: str | None = None,
    ) -> None:
        """Store one answer. ``answer_state`` is derived from the text unless given
        ('not_found' marks an exam question the model never found on any page)."""
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            raise LookupError("Script not found")
        state = answer_state or ("answered" if extracted_text.strip() else "blank")
        with self.db.use() as c:
            c.execute(
                """
                insert into extractions
                    (script_id, question_id, question_number, page_numbers, extracted_text,
                     answer_state, confidence, model, prompt_version, flags)
                values (%s, %s, %s, %s::integer[], %s, %s::answer_state, %s, %s, %s, %s::text[])
                on conflict (script_id, question_id) do update set
                    question_number = excluded.question_number,
                    page_numbers    = excluded.page_numbers,
                    extracted_text  = excluded.extracted_text,
                    answer_state    = excluded.answer_state,
                    confidence      = excluded.confidence,
                    model           = excluded.model,
                    prompt_version  = excluded.prompt_version,
                    flags           = excluded.flags
                """,
                (
                    script_uuid,
                    question_id,
                    question_number,
                    page_numbers or [],
                    extracted_text,
                    state,
                    None if confidence is None else _clamp(confidence, 0.0, 1.0),
                    model,
                    prompt_version,
                    flags or [],
                ),
            )

    def for_script(self, script_id: str, conn: Connection | None = None) -> list[dict[str, Any]]:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return []
        with self.db.use(conn) as c:
            return list(
                c.execute(
                    """
                    select x.id as extraction_id, x.script_id, x.question_id, x.question_number,
                           coalesce(q.question_type::text, 'unknown') as question_type,
                           x.page_numbers, x.page_numbers[1] as page_number,
                           x.extracted_text, x.answer_state::text as answer_state,
                           coalesce(x.model, 'unknown') as extraction_method, x.model,
                           x.prompt_version, x.confidence as extraction_confidence,
                           cardinality(x.flags) > 0 as needs_review,
                           array_to_string(x.flags, ' ') as review_reason, x.flags, x.created_at
                    from extractions x
                    join scripts s on s.id = x.script_id
                    left join exam_questions q
                           on q.exam_id = s.exam_id and q.question_id = x.question_id
                    where x.script_id = %s
                    order by coalesce(x.question_number, 2147483647), x.question_id
                    """,
                    (script_uuid,),
                ).fetchall()
            )

    def answers_for_script(self, script_id: str) -> tuple[dict[str, str], dict[str, int]]:
        """(question_id -> extracted text, question_id -> first page of the answer)."""
        rows = self.for_script(script_id)
        answers = {r["question_id"]: r["extracted_text"] for r in rows}
        pages = {r["question_id"]: r["page_numbers"][0] for r in rows if r["page_numbers"]}
        return answers, pages


def _grader_kind(result: GradeResult) -> str:
    evaluator = (result.evaluator_snapshot or {}).get("evaluator")
    return {"rule": "rule_blank", "deterministic": "deterministic", "slm": "slm"}.get(
        str(evaluator), "llm"
    )


# Reads a grade together with everything the review screens show about it. The snapshot taken
# at grading time wins over the live question/extraction, so what the teacher sees is what the
# model was actually given.
_EVALUATION_SELECT = """
    select es.id as eval_id, es.script_id, q.question_id,
           coalesce(es.grading_snapshot->>'question_text', q.question_text) as question_text,
           coalesce(es.grading_snapshot->>'question_type', q.question_type::text) as question_type,
           coalesce(es.grading_snapshot->>'answer_text', x.extracted_text, '') as answer_text,
           coalesce(nullif(es.grading_snapshot->>'page_number', '')::integer,
                    x.page_numbers[1]) as page_number,
           coalesce(es.grading_snapshot->>'golden_answer', q.golden_answer) as golden_answer,
           coalesce(es.grading_snapshot->'rubric', '[]'::jsonb) as rubric,
           es.max_marks, es.final_marks as awarded_marks, es.ai_marks, es.confidence,
           es.effective_threshold as review_threshold, es.criteria_scores, es.needs_review,
           case when es.review_status = 'teacher_approved' then 'teacher_approved'
                when es.needs_review then 'needs_review'
                else 'scored' end as status,
           es.evidence, es.reasoning, es.llm_output as llm_reasoning, es.model as llm_model,
           es.grading_snapshot->'evaluator' as evaluator_snapshot,
           case when es.review_status = 'teacher_approved' and es.final_marks <> es.ai_marks
                then es.final_marks end as teacher_override,
           es.teacher_reason as teacher_override_reason,
           es.flag_reasons as rule_flags, es.grader_kind::text as grader_kind,
           es.created_at, es.updated_at
    from evaluation_state es
    join scripts s on s.id = es.script_id
    join exam_questions q on q.id = es.exam_question_id
    left join extractions x on x.id = es.extraction_id
"""


class EvaluationRepository:
    """Per-question grades."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        script_id: str,
        spec: QuestionSpec,
        result: GradeResult,
        *,
        rule_flags: list[str] | None = None,
        job_id: str | None = None,
        conn: Connection | None = None,
    ) -> None:
        """Store a grade, replacing an earlier AI grade for the same answer. A teacher's
        decision is final: the WHERE clause makes a re-grade (from any path: jobs, the
        per-script endpoint) leave a teacher-approved answer untouched.

        ``rule_flags`` are the reasons that do not depend on confidence; the confidence flag
        is derived when the grade is read, so it is not stored."""
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            raise LookupError("Script not found")
        marks = round(_clamp(result.awarded_marks, 0.0, spec.max_marks), 2)
        snapshot = {
            "question_text": spec.question_text,
            "question_type": spec.question_type,
            "golden_answer": spec.golden_answer,
            "rubric": spec.criteria,
            "options": spec.options,
            "guidance": spec.guidance,
            "max_marks": spec.max_marks,
            "answer_text": result.answer_text,
            "page_number": result.page_number,
            "evaluator": result.evaluator_snapshot,
        }
        with self.db.use(conn) as c:
            target = c.execute(
                """
                select q.id as exam_question_id,
                       (select x.id from extractions x
                         where x.script_id = s.id and x.question_id = q.question_id)
                         as extraction_id
                from scripts s
                join exam_questions q on q.exam_id = s.exam_id and q.question_id = %s
                where s.id = %s
                """,
                (spec.question_id, script_uuid),
            ).fetchone()
            if target is None:
                raise LookupError(f"Question {spec.question_id} is not part of this script's exam")
            c.execute(
                """
                insert into evaluations
                    (script_id, exam_question_id, extraction_id, job_id, grader_kind, max_marks,
                     ai_marks, final_marks, confidence, flag_reasons, reasoning, evidence,
                     criteria_scores, llm_output, grading_snapshot, model)
                values (%(script)s, %(question)s, %(extraction)s, %(job)s,
                        %(kind)s::grader_kind, %(max)s, %(marks)s, %(marks)s, %(confidence)s,
                        %(flags)s::text[], %(reasoning)s, %(evidence)s, %(criteria)s,
                        %(llm)s, %(snapshot)s, %(model)s)
                on conflict (script_id, exam_question_id) do update set
                    extraction_id    = excluded.extraction_id,
                    job_id           = excluded.job_id,
                    grader_kind      = excluded.grader_kind,
                    max_marks        = excluded.max_marks,
                    ai_marks         = excluded.ai_marks,
                    final_marks      = excluded.final_marks,
                    confidence       = excluded.confidence,
                    flag_reasons     = excluded.flag_reasons,
                    reasoning        = excluded.reasoning,
                    evidence         = excluded.evidence,
                    criteria_scores  = excluded.criteria_scores,
                    llm_output       = excluded.llm_output,
                    grading_snapshot = excluded.grading_snapshot,
                    model            = excluded.model,
                    review_status    = 'unreviewed',
                    reviewed_at      = null,
                    teacher_reason   = null
                where evaluations.review_status <> 'teacher_approved'
                """,
                {
                    "script": script_uuid,
                    "question": target["exam_question_id"],
                    "extraction": target["extraction_id"],
                    "job": as_uuid(job_id) if job_id else None,
                    "kind": _grader_kind(result),
                    "max": spec.max_marks,
                    "marks": marks,
                    "confidence": _clamp(result.confidence, 0.0, 1.0),
                    "flags": rule_flags or [],
                    "reasoning": result.reasoning,
                    "evidence": Jsonb(result.evidence),
                    "criteria": Jsonb(result.criteria_scores),
                    "llm": (
                        Jsonb(result.llm_reasoning) if result.llm_reasoning is not None else None
                    ),
                    "snapshot": Jsonb(snapshot),
                    "model": result.llm_model,
                },
            )

    def delete_ai_grades(self, keys: list[tuple[str, str]]) -> None:
        """Delete the stored grades for (script_id, question_id) pairs ahead of a re-grade.
        A teacher-approved grade is never deleted."""
        rows = [(u, qid) for sid, qid in keys if (u := as_uuid(sid)) is not None]
        if not rows:
            return
        with self.db.use() as c, c.cursor() as cur:
            cur.executemany(
                """
                delete from evaluations ev
                using exam_questions q, scripts s
                where ev.script_id = %s and s.id = ev.script_id and q.id = ev.exam_question_id
                  and q.exam_id = s.exam_id and q.question_id = %s
                  and ev.review_status <> 'teacher_approved'
                """,
                rows,
            )

    def apply_override(
        self, script_id: str, question_id: str, awarded_marks: float, reason: str
    ) -> bool:
        """Record a teacher's decision. The AI's mark (ai_marks) is kept; final_marks becomes the
        teacher's. Approving the AI's mark as-is is an override with the same value."""
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return False
        with self.db.use() as c:
            cursor = c.execute(
                """
                update evaluations ev
                set final_marks = %s, review_status = 'teacher_approved', reviewed_at = now(),
                    teacher_reason = %s
                from exam_questions q
                where ev.script_id = %s and q.id = ev.exam_question_id and q.question_id = %s
                """,
                (awarded_marks, reason, script_uuid, question_id),
            )
            return bool(cursor.rowcount > 0)

    def statuses_for_exam(self, exam_id: str) -> dict[tuple[str, str], str]:
        exam_uuid = as_uuid(exam_id)
        if exam_uuid is None:
            return {}
        with self.db.use() as c:
            rows = c.execute(
                """
                select ev.script_id, q.question_id, ev.review_status::text as status
                from evaluations ev
                join scripts s on s.id = ev.script_id
                join exam_questions q on q.id = ev.exam_question_id
                where s.exam_id = %s
                """,
                (exam_uuid,),
            ).fetchall()
        return {(r["script_id"], r["question_id"]): r["status"] for r in rows}

    @staticmethod
    def _present(row: dict[str, Any]) -> dict[str, Any]:
        """Add the derived fields the API exposes: the full list of flag reasons (rule-based
        ones plus the live confidence one) mirrored into llm_reasoning for older clients."""
        policy = ReviewPolicy(row["review_threshold"])
        reasons = policy.flag_reasons(row.pop("rule_flags"), row["confidence"], row["status"])
        row["flag_reasons"] = reasons
        if isinstance(row["llm_reasoning"], dict):
            row["llm_reasoning"]["flag_reasons"] = reasons
        return row

    def for_script(self, script_id: str, conn: Connection | None = None) -> list[dict[str, Any]]:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return []
        with self.db.use(conn) as c:
            rows = c.execute(
                _EVALUATION_SELECT + " where es.script_id = %s order by q.question_number",
                (script_uuid,),
            ).fetchall()
        return [self._present(r) for r in rows]

    def totals_for_script(self, script_id: str) -> dict[str, Any]:
        script_uuid = as_uuid(script_id)
        if script_uuid is None:
            return {}
        with self.db.use() as c:
            row = c.execute(
                """
                select count(*)::int as total_questions,
                       coalesce(sum(final_marks), 0) as total_awarded,
                       coalesce(sum(max_marks), 0) as total_max,
                       (count(*) filter (where needs_review))::int as needs_review_count,
                       (count(*) filter (where review_status = 'teacher_approved'))::int
                           as approved_count
                from evaluation_state where script_id = %s
                """,
                (script_uuid,),
            ).fetchone()
        return row or {}

    def review_queue(self, exam_id: str | None = None) -> list[dict[str, Any]]:
        exam_uuid = as_uuid(exam_id) if exam_id else None
        if exam_id and exam_uuid is None:
            return []
        with self.db.use() as c:
            rows = c.execute(
                """
                select es.id as eval_id, es.script_id, q.question_id,
                       coalesce(es.grading_snapshot->>'question_text', q.question_text)
                           as question_text,
                       coalesce(nullif(es.grading_snapshot->>'page_number', '')::integer,
                                x.page_numbers[1]) as page_number,
                       coalesce(es.grading_snapshot->>'golden_answer', q.golden_answer)
                           as golden_answer,
                       es.max_marks, es.final_marks as awarded_marks, es.confidence,
                       es.reasoning, es.model as llm_model, es.evidence,
                       es.effective_threshold as review_threshold,
                       es.flag_reasons as rule_flags, es.created_at,
                       s.filename, st.external_id as student_id, s.page_count,
                       st.name as student_name,
                       coalesce(es.grading_snapshot->>'answer_text', x.extracted_text, '')
                           as answer_text,
                       'needs_review' as status
                from evaluation_state es
                join scripts s on s.id = es.script_id
                join students st on st.id = s.student_id
                join exam_questions q on q.id = es.exam_question_id
                left join extractions x on x.id = es.extraction_id
                where es.needs_review and (%(exam)s::uuid is null or s.exam_id = %(exam)s::uuid)
                order by s.filename, q.question_number
                """,
                {"exam": exam_uuid},
            ).fetchall()
        items = []
        for row in rows:
            policy = ReviewPolicy(row["review_threshold"])
            row["flag_reasons"] = policy.flag_reasons(
                row.pop("rule_flags"), row["confidence"], row["status"]
            )
            items.append(row)
        return items

    def stats(self) -> dict[str, int]:
        with self.db.use() as c:
            row = returned(
                c.execute(
                    """
                select (select count(*) from students)::int as total_students,
                       (select count(*) from scripts)::int as total_scripts,
                       (select count(*) from evaluations)::int as total_questions_graded,
                       (select count(*) from evaluation_state where needs_review)::int
                           as needs_review,
                       (select count(*) from evaluations
                         where review_status = 'teacher_approved')::int as approved
                """
                ).fetchone()
            )
        row["scored"] = row["total_questions_graded"] - row["approved"] - row["needs_review"]
        return dict(row)
