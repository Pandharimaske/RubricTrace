"""Reusable rubric template endpoints."""

import json
from uuid import uuid4

from backend.app.db.database import get_db
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/rubric-configs", tags=["rubrics"])


@router.get("")
def list_rubric_configs() -> dict[str, object]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM rubric_configs ORDER BY created_at DESC").fetchall()
    return {"configs": [dict(row) for row in rows]}


@router.post("")
def save_rubric_config(body: dict) -> dict[str, object]:
    config_id = uuid4().hex
    name = body.get("name", "Untitled config")
    config_json = json.dumps(body.get("config", body))
    with get_db() as conn:
        conn.execute(
            "INSERT INTO rubric_configs(config_id, name, config_json) VALUES (?, ?, ?)",
            (config_id, name, config_json),
        )
    return {"config_id": config_id, "name": name}


@router.get("/{config_id}")
def get_rubric_config(config_id: str) -> dict[str, object]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM rubric_configs WHERE config_id=?", (config_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    result = dict(row)
    result["config"] = json.loads(result.pop("config_json", "{}"))
    return result
