"""Batch evaluation of a dataset manifest (CSV/JSON of student answers or script files).

Results are written to a JSON file and checkpointed after every script, so an interrupted run
(Ctrl+C, crash, timeout) never loses more than the one student in flight. Re-running with the
same paths resumes: records already in the output file are skipped.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.app.extraction.reader import ScriptReader, ScriptReading
from backend.app.extraction.segment import normalize_question_id
from backend.app.grading.dataset import DatasetLoader, DatasetRecord
from backend.app.grading.service import AnswerGrader, lookup_answer
from backend.app.llm.errors import ModelUnavailable
from backend.app.models.domain import DEFAULT_REVIEW_THRESHOLD, QuestionSpec

log = logging.getLogger(__name__)


class BatchGrader:
    def __init__(
        self,
        answers: AnswerGrader | None = None,
        reader: ScriptReader | None = None,
        loader: DatasetLoader | None = None,
    ) -> None:
        self.answers = answers or AnswerGrader()
        self.reader = reader or ScriptReader()
        self.loader = loader or DatasetLoader()

    @staticmethod
    def _spec(record: DatasetRecord) -> QuestionSpec:
        return QuestionSpec.from_mapping(
            {
                "question_id": record.question_id,
                "question_text": record.question_text,
                "question_type": record.question_type,
                "golden_answer": record.golden_answer,
                "max_marks": record.max_marks,
                "criteria": [dict(c) for c in record.criteria],
            }
        )

    @staticmethod
    def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        evaluated = [r for r in results if r.get("absolute_error") is not None]
        return {
            "records": len(results),
            "scored": sum(r["status"] == "scored" for r in results),
            "needs_ocr": 0,
            "needs_review": sum(r["status"] == "needs_review" for r in results),
            "manual_mark_records": len(evaluated),
            "mae": round(sum(r["absolute_error"] for r in evaluated) / len(evaluated), 3)
            if evaluated
            else None,
        }

    @staticmethod
    def _model_failed(result: dict[str, Any]) -> bool:
        return any(
            str(reason).startswith(("LLM unavailable", "Automatic grading failed"))
            for reason in result.get("flag_reasons") or []
        )

    @staticmethod
    def _flush(output_path: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"summary": BatchGrader._summary(results), "results": results}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _read_script(
        self, record: DatasetRecord, output_path: Path, question_ids: list[str]
    ) -> ScriptReading:
        log.info("extracting %s (%s)", record.student_id, record.script_path.name)
        reading = self.reader.read(
            record.script_path,
            output_path.parent / "vlm_pages" / record.student_id,
            question_ids=question_ids,
        )
        filled = sum(1 for v in reading.questions.values() if v.strip())
        log.info(
            "extraction done for %s: %d of %d questions have text",
            record.student_id,
            filled,
            len(reading.questions),
        )
        return reading

    def run(
        self,
        metadata_path: Path,
        output_path: Path,
        student_ids: list[str] | None = None,
        review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    ) -> dict[str, Any]:
        """Grade every record in a dataset manifest.

        student_ids: if given, only grade these students (e.g. a quick one-student
        validation run before committing to the full manifest).
        """
        records = self.loader.load(metadata_path)
        if student_ids:
            wanted = set(student_ids)
            records = [r for r in records if r.student_id in wanted]
            if not records:
                raise ValueError(f"No records matched student_ids={student_ids} in {metadata_path}")

        # Distinct question IDs across the manifest, naturally sorted (Q2 before Q10). Only a
        # hint to the VLM about which IDs to look for on a page: it must stay small and
        # de-duplicated (one entry per record bloated the prompt and caused empty extractions).
        question_ids = sorted({r.question_id for r in records}, key=lambda q: (len(q), q))

        results: list[dict[str, Any]] = []
        done_keys: set[tuple[str, str]] = set()
        if output_path.exists():
            try:
                saved = json.loads(output_path.read_text(encoding="utf-8")).get("results", [])
                # A record that failed because the model was unreachable is not a grade, so it
                # is retried instead of being skipped as "done".
                results = [r for r in saved if not self._model_failed(r)]
                done_keys = {(r["student_id"], r["question_id"]) for r in results}
                retrying = len(saved) - len(results)
                if retrying:
                    log.info("Retrying %d record(s) that failed on a previous run", retrying)
            except (json.JSONDecodeError, KeyError):
                results = []
            if done_keys:
                log.info(
                    "Resuming %s: %d student(s) / %d question-results already saved",
                    output_path.name,
                    len({sid for sid, _ in done_keys}),
                    len(done_keys),
                )

        pending = [r for r in records if (r.student_id, r.question_id) not in done_keys]
        readings: dict[Path, ScriptReading] = {}
        last_script: Path | None = None
        students_done = 0
        total_students = len({r.student_id for r in pending})

        for record in pending:
            if last_script is not None and record.script_path != last_script:
                self._flush(output_path, results)
                students_done += 1
                log.info(
                    "[%d/%d] checkpointed -> %s", students_done, total_students, output_path.name
                )

            reading: ScriptReading | None = None
            if record.answer_text is not None:
                answer = record.answer_text
                page_number = None
                extraction: dict[str, Any] = {
                    "method": "metadata_answer",
                    "page_count": None,
                    "ocr_required": False,
                }
            else:
                if record.script_path not in readings:
                    readings[record.script_path] = self._read_script(
                        record, output_path, question_ids
                    )
                reading = readings[record.script_path]
                answer = lookup_answer(reading.questions, record.question_id)
                page_number = reading.question_pages.get(
                    record.question_id
                ) or reading.question_pages.get(f"Q{normalize_question_id(record.question_id)}")
                extraction = {
                    "method": reading.method,
                    "page_count": reading.page_count,
                    "ocr_required": False,
                    "vlm_model": reading.vlm_model,
                }

            spec = self._spec(record)
            try:
                grade = self.answers.grade(spec, answer, review_threshold, page_number)
            except ModelUnavailable as exc:
                grade = self.answers.failed(spec, answer, f"LLM unavailable: {exc}", page_number)

            result = grade.to_dict()
            result.update(
                student_id=record.student_id,
                script_path=str(record.script_path),
                manual_marks=record.manual_marks,
                extracted_answer=answer,
                extraction=extraction,
                absolute_error=(
                    round(abs(grade.awarded_marks - record.manual_marks), 2)
                    if record.manual_marks is not None
                    else None
                ),
            )
            results.append(result)
            last_script = record.script_path
            log.info(
                "%s %s: awarded %s/%s%s page=%s",
                record.student_id,
                record.question_id,
                grade.awarded_marks,
                grade.max_marks,
                f" (manual={record.manual_marks}, err={result['absolute_error']})"
                if result["absolute_error"] is not None
                else "",
                page_number,
            )

        return self._flush(output_path, results)
