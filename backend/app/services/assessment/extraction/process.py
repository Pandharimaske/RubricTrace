"""Process one uploaded script: render pages, transcribe each question's answer via VLM."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.core.settings import PAGE_IMAGE_DIR
from backend.app.db.database import (
    get_db,
    get_extractions_for_script,
    update_script_status,
    upsert_question_extraction,
)
from backend.app.db.exams import get_exam
from backend.app.services.assessment.extraction.questions import extract_script_questions

# Used only for scripts that don't belong to an exam (no answer key to say how many).
DEFAULT_QUESTION_IDS = [f"Q{i}" for i in range(1, 36)]


def process_script_file(
    script_id: str,
    script_path: Path,
    question_ids: list[str] | None = None,
) -> dict[str, Any]:
    question_definitions: list[dict[str, Any]] = []
    with get_db() as conn:
        script = conn.execute(
            "SELECT exam_id FROM scripts WHERE script_id=?", (script_id,)
        ).fetchone()
        if script and script["exam_id"]:
            exam = get_exam(conn, script["exam_id"])
            question_definitions = (exam or {}).get("questions", [])

    # The prompt gets this exam's real question IDs and types when the script
    # belongs to an exam; the fixed default list is only for exam-less scripts.
    exam_question_ids = [str(q["question_id"]) for q in question_definitions]
    question_types = {
        str(q["question_id"]): str(q["question_type"])
        for q in question_definitions
        if q.get("question_type")
    }

    extracted = extract_script_questions(
        script_path,
        PAGE_IMAGE_DIR / script_id,
        None,  # Extraction is page-based; no per-question image directory is used.
        question_ids=question_ids or exam_question_ids or DEFAULT_QUESTION_IDS,
        question_types=question_types or None,
    )
    questions = extracted["questions"]
    question_pages = extracted.get("question_pages", {})
    question_confidences = extracted.get("question_confidences", {})
    conflicts = extracted.get("conflicts") or []
    # Answers the model reported as unreadable are always routed to teacher review,
    # whether or not the exam question has a confidence threshold configured.
    illegible_ids = {
        str(c["question_id"])
        for c in conflicts
        if c.get("kind") == "illegible" and c.get("question_id")
    }
    exam_questions = {str(question["question_id"]): question for question in question_definitions}

    for raw_qid, answer_text in questions.items():
        qid_norm = f"Q{raw_qid}" if not str(raw_qid).upper().startswith("Q") else str(raw_qid)
        exam_question = exam_questions.get(qid_norm, {})
        confidence = question_confidences.get(qid_norm) or question_confidences.get(str(raw_qid))
        threshold = exam_question.get("review_confidence_threshold")
        low_confidence = confidence is not None and threshold is not None and confidence < threshold
        unreadable = qid_norm in illegible_ids
        needs_review = low_confidence or unreadable
        review_reasons: list[str] = []
        if low_confidence:
            review_reasons.append(
                f"Extraction confidence ({confidence:.2f}) is below the question "
                f"threshold ({threshold:.2f})."
            )
        if unreadable:
            review_reasons.append("The answer could not be read reliably from the page image.")
        upsert_question_extraction(
            extraction_id=uuid4().hex,
            script_id=script_id,
            question_id=qid_norm,
            source_path=None,  # Extraction is anchored to the rendered page number.
            extracted_text=answer_text,
            extraction_method=str(extracted["method"]),
            ocr_confidence=None,
            page_number=question_pages.get(qid_norm) or question_pages.get(raw_qid),
            question_number=int("".join(ch for ch in qid_norm if ch.isdigit()) or 0),
            question_type=exam_question.get("question_type", "unknown"),
            vlm_confidence=None,
            extraction_confidence=confidence,
            needs_review=needs_review,
            review_reason=" ".join(review_reasons),
        )

    flagged = [c for c in conflicts if c.get("kind") != "illegible"]
    if flagged:
        print(
            f"[process] script {script_id}: {len(flagged)} item(s) need "
            "manual review — see extraction entries with keys ending in __CONFLICT_pageN "
            "(misread-digit collisions) or starting with Q__UNASSIGNED_pageN "
            "(content that couldn't be attached to any question number)",
            flush=True,
        )

    update_script_status(script_id, "processed", page_count=int(extracted["page_count"]))
    with get_db() as conn:
        conn.execute("UPDATE scripts SET error=NULL WHERE script_id=?", (script_id,))
        stored_extractions = get_extractions_for_script(conn, script_id)
    extracted["extractions"] = {item["question_id"]: item for item in stored_extractions}
    return extracted
