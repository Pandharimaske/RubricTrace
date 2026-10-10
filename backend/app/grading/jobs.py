"""Background jobs for an exam: read (process) its scripts, then grade them.

A single worker thread runs jobs one at a time. That keeps progress reporting simple and
results predictable, and stays well inside the NVIDIA API's free-tier rate limits.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from backend.app.db.exams import ExamRepository, JobRepository
from backend.app.db.repositories import (
    EvaluationRepository,
    ExtractionRepository,
    ScriptRepository,
)
from backend.app.extraction.process import ScriptProcessor
from backend.app.grading.service import GradingService, lookup_answer
from backend.app.llm.errors import ModelUnavailable
from backend.app.models.domain import QuestionSpec

log = logging.getLogger(__name__)


class JobConflict(RuntimeError):
    """A job is already running for this exam."""


class SetupRequired(ValueError):
    """The exam is missing something (questions, reference answers) needed first."""


class NothingToDo(ValueError):
    """No script needs this step."""


class JobRunner:
    def __init__(
        self,
        jobs: JobRepository,
        exams: ExamRepository,
        scripts: ScriptRepository,
        extractions: ExtractionRepository,
        evaluations: EvaluationRepository,
        processor: ScriptProcessor,
        grader: GradingService,
    ) -> None:
        self.jobs = jobs
        self.exams = exams
        self.scripts = scripts
        self.extractions = extractions
        self.evaluations = evaluations
        self.processor = processor
        self.grader = grader
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rubrictrace-job")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ── Starting jobs ────────────────────────────────────────────────────────

    def start_process(self, exam_id: str) -> dict[str, Any]:
        exam = self.exams.get(exam_id)
        if not exam:
            raise LookupError("Exam not found")
        if self.jobs.active(exam_id):
            raise JobConflict("Another job is already running for this exam.")
        if not exam["questions"]:
            raise SetupRequired(
                "Add the questions to the answer key first, so we know how many to look for."
            )

        script_ids = [
            s["script_id"]
            for s in sorted(self.exams.scripts(exam_id), key=lambda s: s["created_at"])
            if s["status"] in ("uploaded", "error", "processing")
        ]
        if not script_ids:
            raise NothingToDo("No scripts are waiting to be processed.")

        question_ids = [q["question_id"] for q in exam["questions"]]
        job = self.jobs.create(exam_id, "process", total=len(script_ids))
        self._executor.submit(self._run_process, job["job_id"], script_ids, question_ids)
        return job

    def start_grade(self, exam_id: str, regrade: bool = False) -> dict[str, Any]:
        exam = self.exams.get(exam_id)
        if not exam:
            raise LookupError("Exam not found")
        if self.jobs.active(exam_id):
            raise JobConflict("Another job is already running for this exam.")

        questions = exam["questions"]
        if not questions:
            raise SetupRequired("Add questions and an answer key before grading.")
        missing = [
            q["question_id"] for q in questions if not (q.get("golden_answer") or "").strip()
        ]
        if missing:
            shown = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
            raise SetupRequired(f"These questions still have no reference answer: {shown}")

        scripts = [
            s
            for s in sorted(self.exams.scripts(exam_id), key=lambda s: s["created_at"])
            if s["status"] in ("processed", "graded", "needs_review")
        ]
        existing = self.evaluations.statuses_for_exam(exam_id)

        plan: list[tuple[str, QuestionSpec]] = []
        for script in scripts:
            for q in questions:
                status = existing.get((script["script_id"], q["question_id"]))
                if status == "teacher_approved":
                    continue  # a teacher's decision is never overwritten
                if status is not None and not regrade:
                    continue
                plan.append((script["script_id"], QuestionSpec.from_mapping(q)))

        if not plan:
            raise NothingToDo(
                "Everything is already graded."
                if scripts
                else "No processed scripts to grade yet. Upload and process scripts first."
            )

        job = self.jobs.create(exam_id, "grade", total=len(plan))
        if regrade:
            # A re-grade replaces the earlier AI marks: clear them now, so the results grid and
            # review queue never mix old and new grades while the job runs. Teacher-approved
            # marks are not in the plan and the delete refuses them as well.
            self.evaluations.delete_ai_grades([(sid, spec.question_id) for sid, spec in plan])
        self._executor.submit(
            self._run_grade, job["job_id"], plan, exam["review_confidence_threshold"]
        )
        return job

    # ── Workers ──────────────────────────────────────────────────────────────

    def _run_process(self, job_id: str, script_ids: list[str], question_ids: list[str]) -> None:
        self.jobs.update(job_id, status="running")
        failures = 0
        try:
            for i, script_id in enumerate(script_ids, start=1):
                if self.jobs.is_cancel_requested(job_id):
                    self.scripts.reset_stuck(script_ids[i - 1 :])
                    self.jobs.update(job_id, status="cancelled", message="")
                    return

                self.jobs.update(job_id, message=f"Reading {self.scripts.label(script_id)}")
                self.scripts.set_status(script_id, "processing")
                script = self.scripts.get(script_id)
                try:
                    self.processor.process(script_id, Path(script["file_path"]), question_ids)  # type: ignore[index]
                except ModelUnavailable as exc:
                    self.scripts.reset_stuck(script_ids[i - 1 :])
                    self.jobs.update(
                        job_id,
                        status="failed",
                        message="",
                        error=f"The vision model is unavailable ({exc}). "
                        "Check NVIDIA_API_KEY and your connection.",
                    )
                    return
                except Exception as exc:  # one bad PDF must not stop the whole batch
                    log.exception("Processing failed for script %s", script_id)
                    failures += 1
                    self.scripts.set_status(script_id, "error", error=str(exc))
                self.jobs.update(job_id, done=i)

            self.jobs.update(
                job_id,
                status="done",
                message="",
                error=f"{failures} script(s) could not be processed" if failures else None,
            )
        except Exception as exc:
            log.exception("Process job %s crashed", job_id)
            self.jobs.update(job_id, status="failed", message="", error=str(exc))

    def _run_grade(
        self,
        job_id: str,
        plan: list[tuple[str, QuestionSpec]],
        exam_threshold: float,
    ) -> None:
        self.jobs.update(job_id, status="running")
        touched: list[str] = []
        answers_cache: dict[str, dict[str, str]] = {}
        pages_cache: dict[str, dict[str, int]] = {}
        labels: dict[str, str] = {}
        failures = 0
        final: dict[str, Any] = {"status": "done", "message": "", "error": None}
        try:
            for i, (script_id, spec) in enumerate(plan, start=1):
                if self.jobs.is_cancel_requested(job_id):
                    final = {"status": "cancelled", "message": "", "error": None}
                    break

                if script_id not in answers_cache:
                    answers_cache[script_id], pages_cache[script_id] = (
                        self.extractions.answers_for_script(script_id)
                    )
                    labels[script_id] = self.scripts.label(script_id)
                if script_id not in touched:
                    touched.append(script_id)

                self.jobs.update(job_id, message=f"{labels[script_id]} · {spec.question_id}")
                answer = lookup_answer(answers_cache[script_id], spec.question_id)
                page_number = pages_cache[script_id].get(spec.question_id)
                try:
                    self.grader.grade_and_store(
                        script_id, spec, answer, exam_threshold, page_number
                    )
                except ModelUnavailable as exc:
                    final = {
                        "status": "failed",
                        "message": "",
                        "error": f"The grading model is unavailable ({exc}). "
                        "Check NVIDIA_API_KEY and your connection.",
                    }
                    break
                except Exception:
                    # Leave this answer ungraded (no placeholder 0 is stored); a plain
                    # "grade" run picks it up again.
                    log.exception("Grading failed for %s %s", script_id, spec.question_id)
                    failures += 1
                self.jobs.update(job_id, done=i)

            if final["status"] == "done" and failures:
                final["error"] = (
                    f"{failures} answer(s) could not be graded and were skipped. "
                    "Run grading again to retry them."
                )
        except Exception as exc:
            log.exception("Grade job %s crashed", job_id)
            final = {"status": "failed", "message": "", "error": str(exc)}
        finally:
            for script_id in touched:
                self.exams.refresh_script_status(script_id)
            self.jobs.update(job_id, **final)
