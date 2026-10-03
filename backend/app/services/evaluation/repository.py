import json
from pathlib import Path
from typing import Any

from backend.app.core.settings import PROCESSED_DIR


def evaluation_path(script_id: str) -> Path:
    return PROCESSED_DIR / "evaluations" / f"{script_id}.json"


def save_evaluation(script_id: str, payload: dict[str, Any]) -> Path:
    path = evaluation_path(script_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_evaluation(script_id: str) -> dict[str, Any]:
    path = evaluation_path(script_id)
    if not path.exists():
        raise FileNotFoundError(f"Evaluation not found: {script_id}")
    return json.loads(path.read_text(encoding="utf-8"))
