"""Reusable rubric template endpoints."""

from typing import Any

from backend.app.api.deps import ContainerDep
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/rubric-configs", tags=["rubrics"])


@router.get("")
def list_rubric_configs(c: ContainerDep) -> dict[str, object]:
    return {"configs": c.rubric_configs.list_all()}


@router.post("")
def save_rubric_config(body: dict[str, Any], c: ContainerDep) -> dict[str, object]:
    name = body.get("name", "Untitled config")
    config_id = c.rubric_configs.save(name, body.get("config", body))
    return {"config_id": config_id, "name": name}


@router.get("/{config_id}")
def get_rubric_config(config_id: str, c: ContainerDep) -> dict[str, object]:
    config = c.rubric_configs.get(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return config
