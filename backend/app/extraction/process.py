"""Process one stored script: render its pages, read every answer with the VLM, persist them."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.db.exams import ExamRepository
from backend.app.db.repositories import ExtractionRepository, ScriptRepository
from backend.app.extraction.reader import ScriptReader, ScriptReading

log = logging.getLogger(__name__)

# Used only for scripts that don't belong to an exam (no answer key to say how many).
DEFAULT_QUESTION_IDS = [f"Q{i}" for i in range(1, 36)]


@dataclass
class ProcessedScript:
    script_id: str
    reading: ScriptReading
    extractions: dict[str, dict[str, Any]]


class ScriptProcessor:
    def __init__(
        self,
        config: Settings,
        reader: ScriptReader,
        scripts: ScriptRepository,
        extractions: ExtractionRepository,
        exams: ExamRepository,
    ) -> None:
        self.config = config
        self.reader = reader
        self.scripts = scripts
        self.extractions = extractions
        self.exams = exams

    def _exam_questions(self, script_id: str) -> list[dict[str, Any]]:
        script = self.scripts.get(script_id)
        if script and script.get("exam_id"):
            exam = self.exams.get(script["exam_id"])
            return (exam or {}).get("questions", [])
        return []

    @staticmethod
    def _normalize_id(raw: str) -> str:
        return raw if str(raw).upper().startswith("Q") else f"Q{raw}"

    def process(
        self,
        script_id: str,
        script_path: Path,
        question_ids: list[str] | None = None,
    ) -> ProcessedScript:
        definitions = self._exam_questions(script_id)
        # The prompt gets this exam's real question IDs and types when the script belongs to an
        # exam; the fixed default list is only for exam-less scripts.
        exam_question_ids = [str(q["question_id"]) for q in definitions]
        question_types = {
            str(q["question_id"]): str(q["question_type"])
            for q in definitions
            if q.get("question_type")
        }
        reading = self.reader.read(
            script_path,
            self.config.page_image_dir / script_id,
            question_ids=question_ids or exam_question_ids or DEFAULT_QUESTION_IDS,
            question_types=question_types or None,
        )

        # Answers the model reported as unreadable always go to teacher review, whether or not
        # the exam question has a confidence threshold configured.
        illegible_ids = {
            str(c["question_id"])
            for c in reading.conflicts
            if c.get("kind") == "illegible" and c.get("question_id")
        }
        by_id = {str(q["question_id"]): q for q in definitions}

        detected: set[str] = set()
        for raw_qid, answer_text in reading.questions.items():
            qid = self._normalize_id(raw_qid)
            detected.add(qid)
            exam_question = by_id.get(qid, {})
            confidence = reading.question_confidences.get(qid) or reading.question_confidences.get(
                str(raw_qid)
            )
            threshold = exam_question.get("review_confidence_threshold")
            low_confidence = (
                confidence is not None and threshold is not None and confidence < threshold
            )
            unreadable = qid in illegible_ids
            reasons: list[str] = []
            if low_confidence:
                reasons.append(
                    f"Extraction confidence ({confidence:.2f}) is below the question "
                    f"threshold ({threshold:.2f})."
                )
            if unreadable:
                reasons.append("The answer could not be read reliably from the page image.")
            page = reading.question_pages.get(qid) or reading.question_pages.get(raw_qid)
            self.extractions.upsert(
                script_id,
                qid,
                answer_text,
                page_numbers=[page] if page else [],
                question_number=int("".join(ch for ch in qid if ch.isdigit()) or 0),
                confidence=confidence,
                model=reading.vlm_model or reading.method,
                flags=reasons,
            )

        # An exam question the model never found on any page is recorded as such, so grading
        # and the review screens can tell "not found" from "found but blank".
        for number, qid in enumerate(exam_question_ids, start=1):
            if qid not in detected:
                self.extractions.upsert(
                    script_id,
                    qid,
                    "",
                    question_number=number,
                    model=reading.vlm_model or reading.method,
                    answer_state="not_found",
                )

        flagged = [c for c in reading.conflicts if c.get("kind") != "illegible"]
        if flagged:
            log.warning(
                "script %s: %d item(s) need manual review - see extraction entries with keys "
                "ending in __CONFLICT_pageN or starting with Q__UNASSIGNED_pageN",
                script_id,
                len(flagged),
            )

        self.scripts.set_status(script_id, "processed", page_count=reading.page_count)
        stored = self.extractions.for_script(script_id)
        return ProcessedScript(script_id, reading, {item["question_id"]: item for item in stored})
