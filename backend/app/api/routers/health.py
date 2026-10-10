"""Health and dashboard summary endpoints."""

from backend.app.api.deps import ContainerDep
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy", "version": "2.0.0"}


@router.get("/stats")
def get_stats(c: ContainerDep) -> dict[str, int]:
    return c.evaluations.stats()
