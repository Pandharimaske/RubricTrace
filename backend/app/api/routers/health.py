"""Health and dashboard summary endpoints."""

from backend.app.db.database import DEFAULT_REVIEW_THRESHOLD, get_db
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "version": "2.0.0"}


@router.get("/stats")
def get_stats() -> dict[str, object]:
    with get_db() as conn:
        total_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        total_scripts = conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
        total_questions = conn.execute("SELECT COUNT(*) FROM question_evaluations").fetchone()[0]
        needs_review = conn.execute(
            "SELECT COUNT(*) FROM question_evaluations qe "
            "JOIN scripts s ON s.script_id = qe.script_id "
            "LEFT JOIN exams e ON e.exam_id = s.exam_id "
            "WHERE qe.status != 'teacher_approved' "
            "AND qe.confidence < COALESCE(qe.threshold_used, e.review_confidence_threshold, ?)",
            (DEFAULT_REVIEW_THRESHOLD,),
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM question_evaluations WHERE status='teacher_approved'"
        ).fetchone()[0]
    return {
        "total_students": total_students,
        "total_scripts": total_scripts,
        "total_questions_graded": total_questions,
        "needs_review": needs_review,
        "approved": approved,
        "scored": total_questions - approved - needs_review,
    }
