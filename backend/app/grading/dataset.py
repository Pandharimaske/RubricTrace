import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetRecord:
    student_id: str
    script_path: Path
    question_id: str
    max_marks: float
    manual_marks: float | None
    criteria: list[dict[str, Any]]
    answer_text: str | None = None
    question_text: str = ""
    golden_answer: str = ""
    question_type: str = ""


def _resolve_script_path(metadata_path: Path, raw_path: str) -> Path:
    script_path = Path(raw_path).expanduser()
    if not script_path.is_absolute():
        script_path = metadata_path.parent / script_path
    return script_path.resolve()


def _parse_record(raw: dict[str, Any], metadata_path: Path) -> DatasetRecord:
    criteria = raw.get("criteria", raw.get("criteria_json", []))
    if isinstance(criteria, str):
        criteria = json.loads(criteria) if criteria.strip() else []
    if not isinstance(criteria, list):
        raise TypeError("criteria must be a JSON list")

    manual_marks = raw.get("manual_marks")
    return DatasetRecord(
        student_id=str(raw["student_id"]),
        script_path=_resolve_script_path(metadata_path, str(raw["script_path"])),
        question_id=str(raw["question_id"]),
        max_marks=float(raw["max_marks"]),
        manual_marks=float(manual_marks) if manual_marks not in (None, "") else None,
        criteria=criteria,
        answer_text=str(raw["answer_text"]) if raw.get("answer_text") else None,
        question_text=str(raw.get("question_text") or ""),
        golden_answer=str(raw.get("golden_answer") or ""),
        question_type=str(raw.get("question_type") or ""),
    )


class DatasetLoader:
    """Loads and validates evaluation datasets from CSV or JSON files."""

    @staticmethod
    def resolve_script_path(metadata_path: Path, raw_path: str) -> Path:
        return _resolve_script_path(metadata_path, raw_path)

    @staticmethod
    def parse_record(raw: dict[str, Any], metadata_path: Path) -> DatasetRecord:
        return _parse_record(raw, metadata_path)

    def load(self, metadata_path: Path) -> list[DatasetRecord]:
        metadata_path = metadata_path.expanduser().resolve()
        if not metadata_path.exists():
            raise FileNotFoundError(f"Dataset metadata not found: {metadata_path}")

        if metadata_path.suffix.lower() == ".csv":
            with metadata_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        elif metadata_path.suffix.lower() == ".json":
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            rows = payload["records"] if isinstance(payload, dict) else payload
        else:
            raise ValueError("Dataset metadata must be CSV or JSON")

        if not rows:
            raise ValueError("Dataset metadata contains no records")
        return [self.parse_record(row, metadata_path) for row in rows]
