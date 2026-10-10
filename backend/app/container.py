"""Composition root: builds every service once, wired to one configuration and database.

The API creates one ``Container`` at startup and hands it to routers via a dependency
(see api/deps.py). Tests build their own with a temporary database and fake model clients.
"""

from __future__ import annotations

from backend.app.core.config import Settings, settings
from backend.app.core.storage import UploadStorage
from backend.app.db.database import Database
from backend.app.db.exams import (
    EvaluatorConfigRepository,
    ExamRepository,
    JobRepository,
)
from backend.app.db.repositories import (
    EvaluationRepository,
    ExtractionRepository,
    RubricConfigRepository,
    ScriptRepository,
    StudentRepository,
)
from backend.app.extraction.pipeline import ScriptExtractor
from backend.app.extraction.process import ScriptProcessor
from backend.app.extraction.reader import ScriptReader
from backend.app.grading.evaluators import EvaluationEngine, LLMEvaluator
from backend.app.grading.file_store import EvaluationFileStore
from backend.app.grading.grader import LLMGrader
from backend.app.grading.jobs import JobRunner
from backend.app.grading.rubric import RubricScorer
from backend.app.grading.service import AnswerGrader, GradingService


class Container:
    def __init__(
        self,
        config: Settings | None = None,
        db: Database | None = None,
        extractor: ScriptExtractor | None = None,
        engine: EvaluationEngine | None = None,
    ) -> None:
        self.config = config or settings
        self.db = db or Database.from_settings(self.config)
        self.storage = UploadStorage.from_settings(self.config)

        self.students = StudentRepository(self.db)
        self.scripts = ScriptRepository(self.db)
        self.extractions = ExtractionRepository(self.db)
        self.evaluations = EvaluationRepository(self.db)
        self.rubric_configs = RubricConfigRepository(self.db)
        self.exams = ExamRepository(self.db)
        self.evaluator_configs = EvaluatorConfigRepository(self.db)
        self.job_records = JobRepository(self.db)
        self.file_store = EvaluationFileStore(self.config.processed_dir)

        self.extractor = extractor or ScriptExtractor(config=self.config)
        self.reader = ScriptReader(self.extractor)
        grader = LLMGrader(config=self.config)
        self.answer_grader = AnswerGrader(engine or EvaluationEngine(LLMEvaluator(grader)))
        self.rubric_scorer = RubricScorer(grader)

        self.grading = GradingService(
            self.config,
            self.answer_grader,
            self.evaluations,
            self.extractions,
            self.scripts,
            self.students,
            self.exams,
            self.reader,
            self.file_store,
        )
        self.processor = ScriptProcessor(
            self.config, self.reader, self.scripts, self.extractions, self.exams
        )
        self.jobs = JobRunner(
            self.job_records,
            self.exams,
            self.scripts,
            self.extractions,
            self.evaluations,
            self.processor,
            self.grading,
        )

    def startup(self) -> None:
        self.db.ensure_schema()
        self.job_records.mark_interrupted()  # a restart kills any job that was mid-run

    def shutdown(self) -> None:
        self.jobs.shutdown()
