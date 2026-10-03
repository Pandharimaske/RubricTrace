"""
Evaluation pipeline for RubricTrace.

evaluate_teacher_config()  — grades one student script with the LLM.
run_pipeline()             — batch-grades records from a CSV/JSON dataset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.core.settings import PAGE_IMAGE_DIR
from backend.app.db.database import (
    DEFAULT_REVIEW_THRESHOLD,
    get_db,
    get_extractions_for_script,
    get_script,
    insert_script,
    review_threshold_for_script,
    review_verdict,
    update_script_status,
    upsert_evaluation,
    upsert_student,
)
from backend.app.extraction.questions import extract_script_questions
from backend.app.extraction.segment import normalize_question_id
from backend.app.grading.dataset import DatasetRecord, load_dataset
from backend.app.grading.grader import llm_grade
from backend.app.grading.repository import save_evaluation
from backend.app.llm.ollama import ModelUnavailable
from backend.app.models.schemas import TeacherEvaluationRequest


def _flag_reasons(
    answer: str,
    llm_result: dict[str, Any],
    max_marks: float,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> list[str]:
    flags: list[str] = []
    if not answer or not answer.strip():
        flags.append("Answer is empty or missing")
    awarded = float(llm_result.get("awarded_marks", 0))
    if awarded > max_marks:
        flags.append("Awarded marks exceed maximum")
    # needs_review comes from confidence alone (never a model-filled flag), judged
    # against the exam's threshold.
    low, reason = review_verdict(llm_result.get("confidence", 0.0), review_threshold)
    if low:
        flags.append(reason.rstrip("."))
    return flags


def _lookup_answer(answers: dict[str, str], question_id: str) -> str:
    direct = answers.get(question_id)
    if direct:
        return direct
    norm = f"Q{normalize_question_id(question_id)}"
    return answers.get(norm, "") or answers.get(normalize_question_id(question_id), "")


def _grade_one_question(
    question_id: str,
    answer: str,
    golden_answer: str,
    criteria_dicts: list[dict[str, Any]],
    max_marks: float,
    guidance: str = "",
    question_text: str = "",
    question_type: str = "",
    page_number: int | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    """Grade a single question with the LLM. Keyword matching is not used."""
    if not answer.strip():
        return {
            "question_id": question_id,
            "question_text": question_text,
            "question_type": question_type,
            "page_number": page_number,
            "answer_text": answer,
            "golden_answer": golden_answer,
            "rubric": criteria_dicts,
            "max_marks": max_marks,
            "awarded_marks": 0.0,
            "confidence": 0.0,
            "needs_review": True,
            "flag_reasons": ["Answer is empty or missing"],
            "evidence": [],
            "reasoning": "No student answer was extracted, so the LLM did not award marks.",
            "llm_reasoning": None,
            "llm_model": None,
            "status": "needs_review",
            "teacher_override": None,
            "teacher_override_reason": None,
            "criteria_scores": [],
            "evaluator_snapshot": None,
        }

    llm_result, llm_model = llm_grade(
        question_id=question_id,
        student_answer=answer,
        golden_answer=golden_answer,
        rubric=criteria_dicts,
        max_marks=max_marks,
        guidance=guidance,
        question_text=question_text,
        question_type=question_type,
    )

    flag_reasons = _flag_reasons(answer, llm_result, max_marks, review_threshold)
    needs_review = bool(flag_reasons)
    awarded_marks = min(float(llm_result["awarded_marks"]), max_marks)
    awarded_marks = max(0.0, round(awarded_marks, 2))

    return {
        "question_id": question_id,
        "question_text": question_text,
        "question_type": question_type,
        "page_number": page_number,
        "answer_text": answer,
        "golden_answer": golden_answer,
        "rubric": criteria_dicts,
        "max_marks": max_marks,
        "awarded_marks": awarded_marks,
        "confidence": float(llm_result["confidence"]),
        "needs_review": needs_review,
        "flag_reasons": flag_reasons,
        "evidence": llm_result.get("evidence") or [],
        "reasoning": llm_result.get("reasoning") or "",
        "llm_reasoning": llm_result,
        "llm_model": llm_model,
        "status": "needs_review" if needs_review else "scored",
        "teacher_override": None,
        "teacher_override_reason": None,
        "criteria_scores": llm_result.get("criteria_scores") or [],
        "evaluator_snapshot": {
            "model": llm_model,
            "question_type": question_type,
        },
    }


def _answers_for_script(script_path: Path, script_id: str) -> tuple[dict[str, str], dict[str, int]]:
    extracted = extract_script_questions(
        script_path,
        PAGE_IMAGE_DIR / script_id,
        None,  # Extraction is page-based; no per-question image directory is used.
    )
    return dict(extracted["questions"]), dict(extracted.get("question_pages", {}))


def evaluate_teacher_config(
    request: TeacherEvaluationRequest,
    script_path: Path,
    output_script_id: str,
    use_db: bool = True,
) -> dict[str, Any]:
    """
    Grade all questions in a script against the teacher's rubric configuration.
    Saves results to SQLite (if use_db) and JSON file.
    """
    answers: dict[str, str] = {}
    pages: dict[str, int] = {}
    review_threshold = DEFAULT_REVIEW_THRESHOLD
    if use_db:
        with get_db() as conn:
            if not get_script(conn, output_script_id):
                upsert_student(output_script_id, output_script_id)
                insert_script(
                    output_script_id, output_script_id, script_path.name, str(script_path)
                )
            review_threshold = review_threshold_for_script(conn, output_script_id)
            for extraction in get_extractions_for_script(conn, output_script_id):
                if extraction.get("extracted_text"):
                    answers[extraction["question_id"]] = extraction["extracted_text"]
                if extraction.get("page_number") is not None:
                    pages[extraction["question_id"]] = extraction["page_number"]

    if not answers or any(
        not _lookup_answer(answers, question.question_id) for question in request.questions
    ):
        fresh_answers, fresh_pages = _answers_for_script(script_path, output_script_id)
        answers.update(fresh_answers)
        pages.update(fresh_pages)

    results: list[dict[str, Any]] = []

    for question in request.questions:
        answer = _lookup_answer(answers, question.question_id)
        criteria_dicts = [c.model_dump() for c in question.criteria]
        guidance = " ".join(c.guidance for c in question.criteria if c.guidance)
        question_threshold = question.review_confidence_threshold or review_threshold

        result = _grade_one_question(
            question_id=question.question_id,
            answer=answer,
            golden_answer=question.golden_answer,
            criteria_dicts=criteria_dicts,
            max_marks=question.max_marks,
            guidance=guidance,
            question_text=question.question_text,
            question_type=question.question_type,
            page_number=pages.get(question.question_id),
            review_threshold=question_threshold,
        )

        if use_db:
            eval_id = str(uuid4())
            upsert_evaluation(
                eval_id=eval_id,
                script_id=output_script_id,
                question_id=question.question_id,
                golden_answer=question.golden_answer,
                rubric=criteria_dicts,
                max_marks=question.max_marks,
                awarded_marks=result["awarded_marks"],
                confidence=result["confidence"],
                status=result["status"],
                evidence=result["evidence"],
                reasoning=result["reasoning"],
                llm_reasoning=result.get("llm_reasoning"),
                llm_model=result.get("llm_model"),
                question_text=question.question_text,
                question_type=question.question_type,
                answer_text=answer,
                page_number=pages.get(question.question_id),
                threshold_used=question_threshold,
                criteria_scores=result.get("criteria_scores"),
                evaluator_snapshot=result.get("evaluator_snapshot"),
            )

        results.append(result)

    payload = {
        "script_id": output_script_id,
        "results": results,
        "total_awarded_marks": round(sum(r["awarded_marks"] for r in results), 2),
        "total_max_marks": round(sum(r["max_marks"] for r in results), 2),
        "needs_review": sum(r["needs_review"] for r in results),
    }

    if use_db:
        status = "needs_review" if payload["needs_review"] > 0 else "graded"
        update_script_status(output_script_id, status)

    save_evaluation(output_script_id, payload)
    return payload


def _score_record(
    record: DatasetRecord,
    answer: str,
    extraction: dict[str, Any],
    page_number: int | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    criteria_dicts = [dict(c) if hasattr(c, "keys") else c for c in record.criteria]
    try:
        result = _grade_one_question(
            question_id=record.question_id,
            answer=answer,
            golden_answer=record.golden_answer,
            criteria_dicts=criteria_dicts,
            max_marks=record.max_marks,
            question_text=record.question_text,
            question_type=record.question_type,
            page_number=page_number,
            review_threshold=review_threshold,
        )
    except ModelUnavailable as exc:
        result = {
            "question_id": record.question_id,
            "question_text": record.question_text,
            "question_type": record.question_type,
            "page_number": page_number,
            "answer_text": answer,
            "golden_answer": record.golden_answer,
            "rubric": criteria_dicts,
            "max_marks": record.max_marks,
            "awarded_marks": 0.0,
            "confidence": 0.0,
            "needs_review": True,
            "flag_reasons": [f"LLM unavailable: {exc}"],
            "evidence": [],
            "reasoning": str(exc),
            "llm_reasoning": None,
            "llm_model": None,
            "status": "needs_review",
            "teacher_override": None,
            "teacher_override_reason": None,
            "criteria_scores": [],
        }
    result.update(
        {
            "student_id": record.student_id,
            "script_path": str(record.script_path),
            "manual_marks": record.manual_marks,
            "extracted_answer": answer,
            "extraction": extraction,
            "absolute_error": (
                round(abs(result["awarded_marks"] - record.manual_marks), 2)
                if record.manual_marks is not None
                else None
            ),
        }
    )
    return result


def run_pipeline(
    metadata_path: Path,
    output_path: Path,
    student_ids: list[str] | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    """
    Batch-grade every record in a dataset manifest.

    Checkpointed: results are written to output_path after each script (student)
    finishes, not just once at the end — so an interrupted run (Ctrl+C, crash,
    timeout) never loses more than the one student that was in flight. Re-running
    with the same metadata_path/output_path resumes: students already fully
    recorded in output_path are skipped.

    student_ids: if given, only grade these students (e.g. for a quick 1-student
    validation run before committing to the full manifest).
    """
    records = load_dataset(metadata_path)
    if student_ids:
        wanted = set(student_ids)
        records = [r for r in records if r.student_id in wanted]
        if not records:
            raise ValueError(f"No records matched student_ids={student_ids} in {metadata_path}")
    document_cache: dict[Path, tuple[dict[str, str], dict[str, int], dict[str, Any]]] = {}
    results: list[dict[str, Any]] = []

    # Distinct question IDs across the whole manifest, naturally sorted (Q2 before
    # Q10). This is only a hint to the VLM about which IDs to look for on a page —
    # it must stay small and de-duplicated. Passing one entry per record (750+
    # mostly-duplicate IDs for a 50-student manifest) bloats the prompt with noise
    # and was silently causing empty extractions for later questions.
    unique_question_ids = sorted(
        {r.question_id for r in records},
        key=lambda q: (len(q), q),
    )

    done_keys: set[tuple[str, str]] = set()
    if output_path.exists():
        try:
            previous = json.loads(output_path.read_text(encoding="utf-8"))
            results = previous.get("results", [])
            done_keys = {(r["student_id"], r["question_id"]) for r in results}
        except (json.JSONDecodeError, KeyError):
            results = []
        if done_keys:
            done_students = {sid for sid, _ in done_keys}
            print(
                f"Resuming {output_path.name}: {len(done_students)} student(s) / "
                f"{len(done_keys)} question-results already saved, skipping those."
            )

    def _flush() -> dict[str, Any]:
        evaluated = [r for r in results if r.get("absolute_error") is not None]
        summary = {
            "records": len(results),
            "scored": sum(r["status"] == "scored" for r in results),
            "needs_ocr": 0,
            "needs_review": sum(r["status"] == "needs_review" for r in results),
            "manual_mark_records": len(evaluated),
            "mae": round(sum(r["absolute_error"] for r in evaluated) / len(evaluated), 3)
            if evaluated
            else None,
        }
        payload = {"summary": summary, "results": results}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    pending = [r for r in records if (r.student_id, r.question_id) not in done_keys]
    total_pending_students = len({r.student_id for r in pending})
    last_script: Path | None = None
    students_done_this_run = 0

    for record in pending:
        if last_script is not None and record.script_path != last_script:
            _flush()
            students_done_this_run += 1
            print(
                f"  [{students_done_this_run}/{total_pending_students}] checkpointed → {output_path.name}"
            )

        if record.answer_text is not None:
            answer = record.answer_text
            extraction = {"method": "metadata_answer", "page_count": None, "ocr_required": False}
            page_number = None
        else:
            if record.script_path not in document_cache:
                print(f"  extracting {record.student_id} ({record.script_path.name})…", flush=True)
                extracted = extract_script_questions(
                    record.script_path,
                    output_path.parent / "vlm_pages" / record.student_id,
                    None,  # Extraction is page-based; no per-question image directory is used.
                    question_ids=unique_question_ids,
                )
                document_cache[record.script_path] = (
                    dict(extracted["questions"]),
                    dict(extracted.get("question_pages", {})),
                    {
                        "method": extracted["method"],
                        "page_count": extracted["page_count"],
                        "ocr_required": False,
                        "vlm_model": extracted["vlm_model"],
                    },
                )
                print(
                    f"  extraction done for {record.student_id}: "
                    f"{sum(1 for v in document_cache[record.script_path][0].values() if v.strip())} "
                    f"of {len(document_cache[record.script_path][0])} questions have text. Grading…",
                    flush=True,
                )
            questions, question_pages, extraction = document_cache[record.script_path]
            answer = _lookup_answer(questions, record.question_id)
            page_number = question_pages.get(record.question_id) or question_pages.get(
                f"Q{normalize_question_id(record.question_id)}"
            )

        result = _score_record(record, answer, extraction, page_number, review_threshold)
        if not answer:
            result["status"] = "needs_review"
        results.append(result)
        last_script = record.script_path
        err = (
            f" (manual={record.manual_marks}, err={result['absolute_error']})"
            if result.get("absolute_error") is not None
            else ""
        )
        print(
            f"    {record.question_id}: awarded {result['awarded_marks']}/{result['max_marks']}"
            f"{err} page={page_number}",
            flush=True,
        )

    return _flush()
