#!/usr/bin/env python3
"""
Import the already-graded Mendeley benchmark (50 students, results_merged.json)
into RubricTrace's database, so the whole benchmark -- exam, answer key, all 50
students' scripts, every question's extracted answer and grade -- shows up in the normal
frontend (Exams -> this exam -> Scripts / Review / Results), like a real class exam.

Why this is needed: grade_mcq.py / grade_short_answer_cloud.py / merge_grading_results.py
only ever write to data/processed/*.json. Nothing in that pipeline touches the database the
frontend reads from. This script is the bridge.

What it does, per student:
  * copies the student's PDF into the upload directory under a stable id, so the page viewer
    and "Process" work exactly as for a real upload (page images render on first view);
  * records the extracted answer text, with the page it came from when
    data/processed/extracted_answers_index.json has it;
  * stores the benchmark's grade as an AI grade (ai_marks = final_marks).

Review state follows the exam's threshold, like any grade: an answer is flagged when its
confidence is below the threshold the benchmark run used (which is copied onto the exam). A
record the benchmark flagged for another reason is kept flagged with that reason.

Idempotent: re-running finds the exam by name and the scripts by their stable ids, and updates
the grades under them. Marks a teacher has approved in the app are never overwritten.

Usage:
    python scripts/import_mendeley_to_db.py
    python scripts/import_mendeley_to_db.py --merged data/processed/results_merged.json \
        --exam-name "Mendeley Benchmark — 50 Students"

The review threshold is taken from the results file's summary when it records one. Pass
--review-threshold to set it explicitly (use the value your grading run used).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import pymupdf
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.config import PROCESSED_DIR, PROJECT_ROOT, settings
from backend.app.db.database import Database
from backend.app.db.exams import ExamRepository
from backend.app.db.repositories import (
    EvaluationRepository,
    ExtractionRepository,
    ScriptRepository,
)
from backend.app.models.domain import (
    QUESTION_TYPES,
    GradeResult,
    QuestionSpec,
    normalize_question_type,
)
from backend.app.models.schemas import ExamUpdate

STUDENT_PDF_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "mendeley_sf3kvjwknt"
    / "archive"
    / "A Dataset of Digitized Student Examination Papers,"
    / "Student_Pdf"
)

EXAM_NAME_DEFAULT = "Mendeley Benchmark — 50 Students"
SOURCE = "mendeley_offline_batch"


def _script_id(exam_id: str, student_id: str) -> str:
    """Stable per (exam, student), so a re-run updates the same rows."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rubrictrace:{exam_id}:{student_id}"))


def _question_number(question_id: str) -> int | None:
    digits = question_id[1:]
    return int(digits) if digits.isdigit() else None


def _criterion_name_and_guidance(criterion: dict[str, Any]) -> tuple[str, str]:
    """A criterion's short name and its guidance, split the way the app splits a rubric it is
    given as JSON: ``"<name> — <how to mark it>"``, else the first 80 characters as the name."""
    text = str(criterion.get("criterion") or criterion.get("name") or "").strip()
    guidance = str(criterion.get("guidance") or "")
    if " — " in text:
        name, _, rest = text.partition(" — ")
        return name.strip(), guidance or rest.strip()
    name = text[:80].strip()
    return name, guidance or (text if text != name else "")


def _criteria(rubric: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The benchmark's rubric entries (``criterion`` / ``points``) as answer-key criteria. A
    criterion has to be worth some marks, so zero-mark entries are dropped."""
    criteria: list[dict[str, Any]] = []
    for c in rubric:
        marks = float(c.get("points", c.get("marks", 0)) or 0)
        if marks > 0:
            name, guidance = _criterion_name_and_guidance(c)
            criteria.append(
                {
                    "name": name or f"Criterion {len(criteria) + 1}",
                    "marks": marks,
                    "expected_concepts": list(c.get("expected_concepts") or []),
                    "guidance": guidance,
                }
            )
    return criteria


_OPTION_LINE = re.compile(r"^\s*\(?([A-Za-z])[.)]\s+(\S.*?)\s*$")


def _split_mcq(question_text: str, golden_answer: str) -> tuple[str, list[dict[str, Any]]]:
    """The stem and the options of an MCQ whose options are written into its text, one per
    line ("A. Little or no autocorrelation"). The option matching the golden letter is correct."""
    stem: list[str] = []
    options: list[dict[str, Any]] = []
    for line in question_text.splitlines():
        match = _OPTION_LINE.match(line)
        if match:
            key = match.group(1).upper()
            options.append(
                {
                    "option_key": key,
                    "option_text": match.group(2),
                    "is_correct": key == golden_answer.strip().upper(),
                    "display_order": len(options),
                }
            )
        elif not options:
            stem.append(line)
        elif line.strip():  # an option that wraps onto a second line
            options[-1]["option_text"] += " " + line.strip()
    return "\n".join(stem).strip(), options


def _build_question_bank(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One answer-key entry per question_id, taken from its first occurrence
    (golden_answer / max_marks / criteria are the same for every student)."""
    bank: dict[str, dict[str, Any]] = {}
    for r in results:
        qid = r["question_id"]
        if qid in bank:
            continue
        qtype = normalize_question_type(r.get("question_type"))
        qtype = qtype if qtype in QUESTION_TYPES else "short_answer"
        text = r.get("question_text", "")
        golden = r.get("golden_answer", "")
        options: list[dict[str, Any]] = []
        criteria: list[dict[str, Any]] = []
        if qtype == "mcq":
            text, options = _split_mcq(text, golden)
        elif qtype != "true_false":
            criteria = _criteria(r.get("rubric") or [])
        bank[qid] = {
            "question_id": qid,
            "question_text": text,
            "golden_answer": golden,
            "question_type": qtype,
            "max_marks": r["max_marks"],
            "options": options,
            "criteria": criteria,
        }
    return [bank[qid] for qid in sorted(bank, key=lambda q: _question_number(q) or 0)]


def _validated(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check the key against the app's own rules (the ones the answer-key editor enforces) and
    return it in the stored shape. Stops with a readable list if anything doesn't conform, rather
    than saving a key the editor would then refuse."""
    # Question by question, so every problem is listed at once instead of one per run.
    problems: list[str] = []
    for q in questions:
        try:
            ExamUpdate.model_validate({"questions": [q]})
        except ValidationError as exc:
            for err in exc.errors():
                msg = str(err["msg"]).removeprefix("Value error, ")
                msg = msg.removeprefix(f"{q['question_id']}: ")  # the app's message may name it
                problems.append(f"  {q['question_id']}: {msg}")
    if problems:
        raise SystemExit(
            "The answer key built from the results file does not meet the app's rules:\n"
            + "\n".join(problems)
        )
    update = ExamUpdate.model_validate({"questions": questions})
    return [q.model_dump() for q in update.questions or []]


def _load_pages(index_path: Path) -> dict[tuple[str, str], int]:
    """(student_id, question_id) -> page, from the VLM extraction index when it exists."""
    if not index_path.is_file():
        print(f"No page index at {index_path}; answers will not link to a page.", flush=True)
        return {}
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    records = payload.get("results", payload)
    return {
        (r["student_id"], r["question_id"]): int(r["page_number"])
        for r in records
        if r.get("page_number") is not None
    }


def _grade_result(
    record: dict[str, Any], spec: QuestionSpec, page: int | None, threshold: float
) -> tuple[GradeResult, list[str]]:
    """The benchmark's grade as a GradeResult, plus the rule-based flags to store with it."""
    is_mcq = spec.question_type == "mcq"
    confidence = float(record.get("confidence") or 0.0)
    review_reason = record.get("review_reason") or ""
    reasoning = record.get("reasoning") or (review_reason if is_mcq else "")
    evidence = record.get("evidence") or ([review_reason] if is_mcq and review_reason else [])

    # Confidence flags are derived from the threshold when a grade is read. Anything the
    # benchmark flagged beyond that is kept as an explicit reason.
    rule_flags: list[str] = []
    if record.get("needs_review") and confidence >= threshold:
        rule_flags = [review_reason or "Flagged by the benchmark grading run."]

    result = GradeResult(
        question_id=spec.question_id,
        question_text=spec.question_text,
        question_type=spec.question_type,
        page_number=page,
        answer_text=record.get("extracted_answer", "") or "",
        golden_answer=spec.golden_answer,
        rubric=spec.criteria,
        max_marks=spec.max_marks,
        awarded_marks=float(record["awarded_marks"]),
        confidence=confidence,
        needs_review=bool(record.get("needs_review")),
        flag_reasons=rule_flags,
        evidence=list(evidence),
        reasoning=reasoning,
        llm_reasoning=None,
        llm_model=record.get("llm_model"),
        status=record.get("status") or "scored",
        criteria_scores=list(record.get("criteria_scores") or []),
        evaluator_snapshot={
            "evaluator": "deterministic" if is_mcq else "llm",
            "source": SOURCE,
        },
    )
    return result, rule_flags


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--merged", type=Path, default=PROCESSED_DIR / "results_merged.json")
    parser.add_argument(
        "--index", type=Path, default=PROCESSED_DIR / "extracted_answers_index.json"
    )
    parser.add_argument("--exam-name", default=EXAM_NAME_DEFAULT)
    parser.add_argument(
        "--review-threshold",
        type=float,
        default=None,
        help="Review confidence threshold for the exam (default: the results file's summary).",
    )
    args = parser.parse_args()

    payload = json.loads(args.merged.read_text(encoding="utf-8"))
    results = payload["results"]
    print(f"Loaded {len(results)} graded records from {args.merged}", flush=True)
    pages = _load_pages(args.index)

    settings.ensure_data_dirs()
    db = Database.from_settings(settings)
    try:
        db.check_schema()
        exams = ExamRepository(db)
        scripts = ScriptRepository(db, settings.upload_dir)
        extractions = ExtractionRepository(db)
        evaluations = EvaluationRepository(db)

        existing = exams.find_by_name(args.exam_name)
        exam_id = str((existing or exams.create(args.exam_name))["exam_id"])
        print(f"Exam: {args.exam_name!r} ({exam_id})", flush=True)

        questions = _validated(_build_question_bank(results))
        # The review threshold is part of the exam's configuration; use the one the grading
        # run used so the imported exam flags what the benchmark flagged.
        review_threshold = args.review_threshold
        if review_threshold is None:
            review_threshold = (payload.get("summary") or {}).get("review_confidence_threshold")
        if review_threshold is None:
            print(
                "Note: the results file records no review threshold, so the exam keeps its "
                "current one. Pass --review-threshold to match your grading run.",
                flush=True,
            )
        exam = exams.update(
            exam_id, questions=questions, review_confidence_threshold=review_threshold
        )
        if exam is None:
            raise SystemExit("The exam disappeared while importing.")
        threshold = float(exam["review_confidence_threshold"])
        specs = {q["question_id"]: QuestionSpec.from_mapping(q) for q in questions}
        print(
            f"Answer key set: {len(questions)} questions, review threshold {threshold}", flush=True
        )

        by_student: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            by_student.setdefault(r["student_id"], []).append(r)

        for student_id, records in sorted(
            by_student.items(), key=lambda kv: int(kv[0].split("_")[1])
        ):
            script_id = _script_id(exam_id, student_id)
            pdf = STUDENT_PDF_DIR / f"{student_id}.pdf"
            local = settings.upload_dir / f"{script_id}.pdf"

            if not local.is_file():
                if pdf.is_file():
                    shutil.copy2(pdf, local)
                else:
                    print(f"  WARNING {student_id}: PDF not found at {pdf}", flush=True)

            page_count = None
            if local.is_file():
                with pymupdf.open(local) as doc:
                    page_count = doc.page_count

            # One transaction per student: a handful of round trips however many answers there
            # are (the database is remote), and a student is imported completely or not at all.
            extraction_rows: list[dict[str, Any]] = []
            grades: list[tuple[QuestionSpec, GradeResult, list[str]]] = []
            for r in records:
                qid = r["question_id"]
                page = pages.get((student_id, qid))
                extraction_rows.append(
                    {
                        "question_id": qid,
                        "extracted_text": r.get("extracted_answer", "") or "",
                        "page_numbers": [page] if page else [],
                        "question_number": _question_number(qid),
                        "model": SOURCE,
                    }
                )
                result, rule_flags = _grade_result(r, specs[qid], page, threshold)
                grades.append((specs[qid], result, rule_flags))

            with db.connection() as conn:
                if scripts.get(script_id, conn) is None:
                    scripts.insert(
                        script_id,
                        student_id,
                        pdf.name,
                        str(local),
                        exam_id,
                        student_name=student_id.replace("_", " "),
                        conn=conn,
                    )
                scripts.set_status(script_id, "processed", page_count=page_count, conn=conn)
                extractions.upsert_many(script_id, extraction_rows, conn=conn)
                evaluations.upsert_many(script_id, grades, conn=conn)

            print(f"  {student_id}: {len(records)} questions imported", flush=True)

        # Reconcile: the imported exam should flag what the benchmark flagged.
        benchmark_flagged = sum(1 for r in results if r.get("needs_review"))
        queued = len(evaluations.review_queue(exam_id))
        print(
            f"\nThe benchmark flagged {benchmark_flagged} answers; the review queue now "
            f"holds {queued}.",
            flush=True,
        )
        if queued != benchmark_flagged:
            print(
                "These differ: check --review-threshold, or whether teacher-approved marks "
                "from an earlier import account for it.",
                flush=True,
            )
    finally:
        db.close()

    print(
        f"\nDone. Open the app -> Exams -> {args.exam_name!r} to see all {len(by_student)} students.",
        flush=True,
    )


if __name__ == "__main__":
    main()
