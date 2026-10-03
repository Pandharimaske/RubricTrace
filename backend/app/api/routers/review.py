"""Teacher review queue endpoints."""

import json

from backend.app.db.database import (
    DEFAULT_REVIEW_THRESHOLD,
    clamp_review_threshold,
    effective_flag_reasons,
    get_db,
)
from fastapi import APIRouter

router = APIRouter(prefix="/review-queue", tags=["review"])


@router.get("")
def get_review_queue(exam_id: str | None = None) -> dict[str, object]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT qe.eval_id, qe.script_id, qe.question_id, qe.question_text,
                qe.page_number, qe.golden_answer, qe.max_marks, qe.awarded_marks,
                qe.confidence, qe.status, qe.reasoning, qe.llm_model,
                qe.evidence_json, qe.llm_reasoning_json, qe.created_at,
                s.filename, s.student_id, s.page_count, st.name AS student_name,
                qe_source.source_path, qe_source.extracted_text AS answer_text,
                COALESCE(qe.threshold_used, e.review_confidence_threshold, ?) AS review_threshold
            FROM question_evaluations qe
            JOIN scripts s ON qe.script_id = s.script_id
            LEFT JOIN exams e ON e.exam_id = s.exam_id
            LEFT JOIN students st ON s.student_id = st.student_id
            LEFT JOIN question_extractions qe_source
                ON qe.script_id = qe_source.script_id AND qe.question_id = qe_source.question_id
            WHERE qe.status != 'teacher_approved'
              AND qe.confidence < COALESCE(qe.threshold_used, e.review_confidence_threshold, ?)
              AND (? IS NULL OR s.exam_id = ?)
            ORDER BY s.filename, qe.question_id
            """,
            (DEFAULT_REVIEW_THRESHOLD, DEFAULT_REVIEW_THRESHOLD, exam_id, exam_id),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json", "[]"))
        except (json.JSONDecodeError, TypeError, KeyError):
            item["evidence"] = []
        try:
            llm = json.loads(item.pop("llm_reasoning_json", None) or "{}")
            stored = llm.get("flag_reasons", []) if isinstance(llm, dict) else []
        except (json.JSONDecodeError, TypeError):
            stored = []
        threshold = clamp_review_threshold(item["review_threshold"])
        item["review_threshold"] = threshold
        item["status"] = "needs_review"
        item["flag_reasons"] = effective_flag_reasons(
            stored, item["confidence"], threshold, item["status"]
        )
        items.append(item)
    return {"total": len(items), "items": items}
