"""JSON evaluation files: a per-script snapshot of the latest grading payload, kept for scripts
that were graded outside an exam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.core.config import settings


class EvaluationFileStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or settings.processed_dir) / "evaluations"

    def path_for(self, script_id: str) -> Path:
        return self.base_dir / f"{script_id}.json"

    def save(self, script_id: str, payload: dict[str, Any]) -> Path:
        path = self.path_for(script_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def load(self, script_id: str) -> dict[str, Any]:
        path = self.path_for(script_id)
        if not path.exists():
            raise FileNotFoundError(f"Evaluation not found: {script_id}")
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
