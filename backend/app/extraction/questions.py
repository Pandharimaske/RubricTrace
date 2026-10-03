"""Turn a student script into per-question transcribed answers via the VLM."""

from __future__ import annotations

from pathlib import Path

from backend.app.extraction.pdf import pdf_to_images
from backend.app.extraction.pipeline import vlm_extract_questions
from backend.app.extraction.segment import (
    normalize_question_id,
    segment_questions,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def render_script_pages(script_path: Path, output_dir: Path) -> list[Path]:
    suffix = script_path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_images(script_path, output_dir)
    if suffix in IMAGE_SUFFIXES:
        output_dir.mkdir(parents=True, exist_ok=True)
        page_path = output_dir / f"{script_path.stem}_page_1{suffix}"
        page_path.write_bytes(script_path.read_bytes())
        return [page_path]
    raise ValueError(f"Unsupported script type for page rendering: {suffix or 'none'}")


def _qid(raw: str) -> str:
    norm = normalize_question_id(raw)
    return f"Q{norm}"


def extract_script_questions(
    script_path: Path,
    page_dir: Path,
    crop_dir: Path | None = None,
    question_ids: list[str] | None = None,
    question_types: dict[str, str] | None = None,
) -> dict[str, object]:
    """
    Extract per-question student answers.

    Plain text files are split on question headings (layout, not grading).
    PDFs and images are read by the VLM directly without cropping, one structured
    call per page. `question_types` (question_id -> mcq/short_answer/...) tells the
    model how to format each answer.
    """
    suffix = script_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        text = script_path.read_text(encoding="utf-8")
        raw = segment_questions(text)
        questions = {_qid(qid): answer for qid, answer in raw.items()}
        return {
            "questions": questions,
            "question_pages": {},
            "question_confidences": {},
            "pages": [],
            "extractions": {},
            "method": "text_file",
            "vlm_model": None,
            "page_count": 1,
            "conflicts": [],
        }

    page_paths = render_script_pages(script_path, page_dir)
    questions, question_pages, vlm_model, conflicts, question_confidences = vlm_extract_questions(
        page_paths, question_ids, question_types=question_types
    )

    return {
        "questions": questions,
        "question_pages": question_pages,
        "question_confidences": question_confidences,
        "pages": [str(path) for path in page_paths],
        "extractions": {},
        "method": "ollama_vlm",
        "vlm_model": vlm_model,
        "page_count": len(page_paths),
        "conflicts": conflicts,
    }
