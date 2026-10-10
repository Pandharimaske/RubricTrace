"""Teacher review queue endpoints."""

from backend.app.api.deps import ContainerDep
from fastapi import APIRouter

router = APIRouter(prefix="/review-queue", tags=["review"])


@router.get("")
def get_review_queue(c: ContainerDep, exam_id: str | None = None) -> dict[str, object]:
    items = c.evaluations.review_queue(exam_id)
    return {"total": len(items), "items": items}
