"""Turn a student script file into per-question answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.extraction.pdf import pdf_to_images
from backend.app.extraction.pipeline import ScriptExtractor
from backend.app.extraction.segment import normalize_question_id, segment_questions

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})
TEXT_SUFFIXES = frozenset({".txt", ".md"})


@dataclass
class ScriptReading:
    questions: dict[str, str]
    question_pages: dict[str, int] = field(default_factory=dict)
    question_confidences: dict[str, float] = field(default_factory=dict)
    pages: list[str] = field(default_factory=list)
    method: str = "vlm"
    vlm_model: str | None = None
    page_count: int = 1
    conflicts: list[dict[str, Any]] = field(default_factory=list)


class ScriptReader:
    """Plain text files are split on question headings (layout, not grading). PDFs and images
    are rendered to page images and read by the VLM directly, one structured call per page."""

    def __init__(self, extractor: ScriptExtractor | None = None) -> None:
        self.extractor = extractor or ScriptExtractor()

    @staticmethod
    def render_pages(script_path: Path, output_dir: Path) -> list[Path]:
        suffix = script_path.suffix.lower()
        if suffix == ".pdf":
            return pdf_to_images(script_path, output_dir)
        if suffix in IMAGE_SUFFIXES:
            output_dir.mkdir(parents=True, exist_ok=True)
            page_path = output_dir / f"{script_path.stem}_page_1{suffix}"
            page_path.write_bytes(script_path.read_bytes())
            return [page_path]
        raise ValueError(f"Unsupported script type for page rendering: {suffix or 'none'}")

    def read(
        self,
        script_path: Path,
        page_dir: Path,
        question_ids: list[str] | None = None,
        question_types: dict[str, str] | None = None,
    ) -> ScriptReading:
        if script_path.suffix.lower() in TEXT_SUFFIXES:
            raw = segment_questions(script_path.read_text(encoding="utf-8"))
            return ScriptReading(
                questions={f"Q{normalize_question_id(qid)}": text for qid, text in raw.items()},
                method="text_file",
            )

        page_paths = self.render_pages(script_path, page_dir)
        outcome = self.extractor.extract_pages(page_paths, question_ids, question_types)
        return ScriptReading(
            questions=outcome.questions,
            question_pages=outcome.question_pages,
            question_confidences=outcome.question_confidences,
            pages=[str(p) for p in page_paths],
            method="vlm",
            vlm_model=outcome.model,
            page_count=len(page_paths),
            conflicts=outcome.conflicts,
        )
