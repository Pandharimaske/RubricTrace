"""Background jobs for an exam: segment/transcribe its scripts, then grade them.

A single worker thread runs jobs one at a time. Local Ollama models serve one request
at a time anyway, and it keeps progress reporting simple and results predictable.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.db.database import (
    get_db,
    get_extractions_for_script,
    review_threshold_for_script,
    upsert_evaluation,
)
from backend.app.db.exams import (
    active_job,
    create_job,
    get_exam,
    is_cancel_requested,
    update_job,
)
from backend.app.services.assessment.extraction.process import process_script_file
from backend.app.services.assessment.jobs.pipeline import _grade_one_question, _lookup_answer
from backend.app.services.assessment.llm.ollama import ModelUnavailable

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rubrictrace-job")


class JobConflict(RuntimeError):
    """A job is already running for this exam."""


class SetupRequired(ValueError):
    """The exam is missing something (questions, reference answers) needed first."""


class NothingToDo(ValueError):
    """No script needs this step."""


# ── Starting jobs ────────────────────────────────────────────────────────────


def start_process_job(exam_id: str) -> dict[str, Any]:
    with get_db() as conn:
        exam = get_exam(conn, exam_id)
        if not exam:
            raise LookupError("Exam not found")
        if active_job(conn, exam_id):
            raise JobConflict("Another job is already running for this exam.")
        if not exam["questions"]:
            raise SetupRequired(
                "Add the questions to the answer key first, so we know how many to look for."
            )
        rows = conn.execute(
            "SELECT script_id FROM scripts WHERE exam_id=? "
            "AND status IN ('uploaded', 'error', 'processing') ORDER BY created_at",
            (exam_id,),
        ).fetchall()

    script_ids = [r["script_id"] for r in rows]
    if not script_ids:
        raise NothingToDo("No scripts are waiting to be processed.")

    question_ids = [q["question_id"] for q in exam["questions"]]
    job = create_job(exam_id, "process", total=len(script_ids))
    _executor.submit(_run_process, job["job_id"], script_ids, question_ids)
    return job


def start_grade_job(exam_id: str, regrade: bool = False) -> dict[str, Any]:
    with get_db() as conn:
        exam = get_exam(conn, exam_id)
        if not exam:
            raise LookupError("Exam not found")
        if active_job(conn, exam_id):
            raise JobConflict("Another job is already running for this exam.")

        questions = exam["questions"]
        if not questions:
            raise SetupRequired("Add questions and an answer key before grading.")
        missing = [
            q["question_id"] for q in questions if not (q.get("golden_answer") or "").strip()
        ]
        if missing:
            shown = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
            raise SetupRequired(f"These questions still have no reference answer: {shown}")

        scripts = conn.execute(
            "SELECT script_id FROM scripts WHERE exam_id=? "
            "AND status IN ('processed', 'graded', 'needs_review') ORDER BY created_at",
            (exam_id,),
        ).fetchall()
        existing = {
            (r["script_id"], r["question_id"]): r["status"]
            for r in conn.execute(
                "SELECT qe.script_id, qe.question_id, qe.status FROM question_evaluations qe "
                "JOIN scripts s ON s.script_id = qe.script_id WHERE s.exam_id=?",
                (exam_id,),
            )
        }

    plan: list[tuple[str, dict[str, Any]]] = []
    for script in scripts:
        for q in questions:
            status = existing.get((script["script_id"], q["question_id"]))
            if status == "teacher_approved":
                continue  # a teacher's decision is never overwritten
            if status is not None and not regrade:
                continue
            plan.append((script["script_id"], q))

    if not plan:
        raise NothingToDo(
            "Everything is already graded."
            if scripts
            else "No processed scripts to grade yet. Upload and process scripts first."
        )

    job = create_job(exam_id, "grade", total=len(plan))
    _executor.submit(_run_grade, job["job_id"], plan)
    return job


# ── Workers ──────────────────────────────────────────────────────────────────


def _label(script_id: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT sc.filename, st.name FROM scripts sc "
            "LEFT JOIN students st ON st.student_id = sc.student_id WHERE sc.script_id=?",
            (script_id,),
        ).fetchone()
    return (row["name"] or row["filename"]) if row else script_id


def _set_script_status(script_id: str, status: str, error: str | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE scripts SET status=?, error=? WHERE script_id=?", (status, error, script_id)
        )


def _run_process(job_id: str, script_ids: list[str], question_ids: list[str]) -> None:
    update_job(job_id, status="running")
    failures = 0
    try:
        for i, script_id in enumerate(script_ids, start=1):
            if is_cancel_requested(job_id):
                _reset_unfinished(script_ids[i - 1 :])
                update_job(job_id, status="cancelled", message="")
                return

            update_job(job_id, message=f"Reading {_label(script_id)}")
            _set_script_status(script_id, "processing")
            with get_db() as conn:
                row = conn.execute(
                    "SELECT file_path FROM scripts WHERE script_id=?", (script_id,)
                ).fetchone()
            try:
                process_script_file(script_id, Path(row["file_path"]), question_ids)
            except ModelUnavailable as exc:
                _reset_unfinished(script_ids[i - 1 :])
                update_job(
                    job_id,
                    status="failed",
                    message="",
                    error=f"The vision model is unavailable ({exc}). Is Ollama running?",
                )
                return
            except Exception as exc:  # one bad PDF must not stop the whole batch
                log.exception("Processing failed for script %s", script_id)
                failures += 1
                _set_script_status(script_id, "error", str(exc))
            update_job(job_id, done=i)

        update_job(
            job_id,
            status="done",
            message="",
            error=f"{failures} script(s) could not be processed" if failures else None,
        )
    except Exception as exc:
        log.exception("Process job %s crashed", job_id)
        update_job(job_id, status="failed", message="", error=str(exc))


def _reset_unfinished(script_ids: list[str]) -> None:
    with get_db() as conn:
        conn.executemany(
            "UPDATE scripts SET status='uploaded' WHERE script_id=? AND status='processing'",
            [(sid,) for sid in script_ids],
        )


def _grade_and_store(
    script_id: str, q: dict[str, Any], answer: str, page_number: int | None = None
) -> None:
    criteria = q.get("criteria") or []
    guidance = " ".join(c.get("guidance", "") for c in criteria if c.get("guidance"))
    with get_db() as conn:
        review_threshold = review_threshold_for_script(conn, script_id)
    result = _grade_one_question(
        question_id=q["question_id"],
        answer=answer,
        golden_answer=q.get("golden_answer", ""),
        criteria_dicts=criteria,
        max_marks=q["max_marks"],
        guidance=guidance,
        question_text=q.get("question_text", ""),
        question_type=q.get("question_type", ""),
        page_number=page_number,
        review_threshold=review_threshold,
    )
    # Keep the reasons the answer was flagged next to the model's own output,
    # so the review screen can say *why* it needs a teacher.
    llm_reasoning = dict(result.get("llm_reasoning") or {})
    llm_reasoning["flag_reasons"] = result.get("flag_reasons") or []
    upsert_evaluation(
        eval_id=uuid4().hex,
        script_id=script_id,
        question_id=q["question_id"],
        golden_answer=q.get("golden_answer", ""),
        rubric=criteria,
        max_marks=q["max_marks"],
        awarded_marks=result["awarded_marks"],
        confidence=result["confidence"],
        status=result["status"],
        evidence=result["evidence"],
        reasoning=result["reasoning"],
        llm_reasoning=llm_reasoning,
        llm_model=result.get("llm_model"),
        question_text=q.get("question_text", ""),
        page_number=page_number,
    )


def _store_failure(
    script_id: str, q: dict[str, Any], exc: Exception, page_number: int | None = None
) -> None:
    """If the model errors on one answer, hand it to the teacher instead of losing it."""
    # Note: status is stored as "needs_review" but the actual needs_review flag
    # is derived at read-time from confidence (0.0) and the exam's threshold
    upsert_evaluation(
        eval_id=uuid4().hex,
        script_id=script_id,
        question_id=q["question_id"],
        golden_answer=q.get("golden_answer", ""),
        rubric=q.get("criteria") or [],
        max_marks=q["max_marks"],
        awarded_marks=0.0,
        confidence=0.0,
        status="needs_review",
        evidence=[],
        reasoning=f"Automatic grading failed ({type(exc).__name__}: {exc}). Please grade manually.",
        llm_reasoning={"flag_reasons": ["Automatic grading failed"]},
        question_text=q.get("question_text", ""),
        page_number=page_number,
    )


def _refresh_script_status(script_id: str) -> None:
    with get_db() as conn:
        # Pending = not teacher-approved and below the exam's confidence threshold.
        threshold = review_threshold_for_script(conn, script_id)
        row = conn.execute(
            "SELECT COUNT(*) AS n, "
            "COALESCE(SUM(CASE WHEN status != 'teacher_approved' AND confidence < ? "
            "THEN 1 ELSE 0 END), 0) AS pending "
            "FROM question_evaluations WHERE script_id=?",
            (threshold, script_id),
        ).fetchone()
        if row["n"]:
            status = "needs_review" if row["pending"] else "graded"
            conn.execute("UPDATE scripts SET status=? WHERE script_id=?", (status, script_id))


def _run_grade(job_id: str, plan: list[tuple[str, dict[str, Any]]]) -> None:
    update_job(job_id, status="running")
    touched: list[str] = []
    answers_cache: dict[str, dict[str, str]] = {}
    pages_cache: dict[str, dict[str, int]] = {}
    labels: dict[str, str] = {}
    failures = 0
    final: dict[str, Any] = {"status": "done", "message": "", "error": None}
    try:
        for i, (script_id, q) in enumerate(plan, start=1):
            if is_cancel_requested(job_id):
                final = {"status": "cancelled", "message": "", "error": None}
                break

            if script_id not in answers_cache:
                with get_db() as conn:
                    extractions = get_extractions_for_script(conn, script_id)
                    answers_cache[script_id] = {
                        item["question_id"]: item["extracted_text"] for item in extractions
                    }
                    pages_cache[script_id] = {
                        item["question_id"]: item["page_number"]
                        for item in extractions
                        if item.get("page_number") is not None
                    }
                labels[script_id] = _label(script_id)
            if script_id not in touched:
                touched.append(script_id)

            update_job(job_id, message=f"{labels[script_id]} · {q['question_id']}")
            answer = _lookup_answer(answers_cache[script_id], q["question_id"])
            page_number = pages_cache[script_id].get(q["question_id"])
            try:
                _grade_and_store(script_id, q, answer, page_number)
            except ModelUnavailable as exc:
                final = {
                    "status": "failed",
                    "message": "",
                    "error": f"The grading model is unavailable ({exc}). Is Ollama running?",
                }
                break
            except Exception as exc:
                log.exception("Grading failed for %s %s", script_id, q["question_id"])
                failures += 1
                _store_failure(script_id, q, exc, page_number)
            update_job(job_id, done=i)

        if final["status"] == "done" and failures:
            final["error"] = f"{failures} answer(s) failed and were sent to review"
    except Exception as exc:
        log.exception("Grade job %s crashed", job_id)
        final = {"status": "failed", "message": "", "error": str(exc)}
    finally:
        for script_id in touched:
            _refresh_script_status(script_id)
        update_job(job_id, **final)
