#!/usr/bin/env python3
"""
Import the already-graded Mendeley benchmark (50 students, results_merged.json)
into RubricTrace's real database, so the whole benchmark — exam, answer key,
all 50 students' scripts, every question's extracted answer and grade — shows
up in the normal frontend (Exams -> this exam -> Scripts / Review / Results),
exactly like a real class exam would.

Why this is needed: grade_mcq_local.py / grade_short_answer_cloud.py /
merge_grading_results.py only ever write to data/processed/*.json. Nothing in
that pipeline touches the students/scripts/question_extractions/question_evaluations
tables the frontend actually reads from (see backend/app/db/database.py,
backend/app/db/exams.py, and the scripts router). This
script is the missing bridge.

Idempotent: re-running finds the exam by name (instead of creating a
duplicate) and upserts everything under it, so it's safe to re-run after a
fresh grading pass.

Usage:
    python scripts/import_mendeley_to_db.py
    python scripts/import_mendeley_to_db.py --merged data/processed/results_merged.json \
        --exam-name "Mendeley Benchmark — 50 Students"

Known gap: this does NOT populate PAGE_IMAGE_DIR, because the offline batch
pipeline (grade_mcq_local.py etc.) never rendered/stored page images — it read
straight from data/raw/extracted_answers/. So GET /scripts/{id}/pages/{n}/image
will 404 for these scripts; every question's extracted TEXT (via question_extractions)
and grade (via question_evaluations) will still show correctly everywhere else.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.settings import PROCESSED_DIR, PROJECT_ROOT
from backend.app.db.database import get_db
from backend.app.db.exams import create_exam, list_exams, update_exam

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


def _find_or_create_exam(name: str) -> str:
    with get_db() as conn:
        for exam in list_exams(conn):
            if exam["name"] == name:
                return exam["exam_id"]
    exam = create_exam(name)
    return exam["exam_id"]


def _build_question_bank(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One answer-key entry per question_id, taken from its first occurrence
    (golden_answer / max_marks / criteria are the same for every student)."""
    bank: dict[str, dict[str, Any]] = {}
    for r in results:
        qid = r["question_id"]
        if qid in bank:
            continue
        bank[qid] = {
            "question_id": qid,
            "question_text": r.get("question_text", ""),
            "golden_answer": r.get("golden_answer", ""),
            "question_type": r.get("question_type", ""),
            "max_marks": r["max_marks"],
            "criteria": r.get("rubric", []) or [],
        }
    return [bank[qid] for qid in sorted(bank, key=lambda q: int(q[1:]))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--merged", type=Path, default=PROCESSED_DIR / "results_merged.json")
    parser.add_argument("--exam-name", default=EXAM_NAME_DEFAULT)
    args = parser.parse_args()

    payload = json.loads(args.merged.read_text(encoding="utf-8"))
    results = payload["results"]
    print(f"Loaded {len(results)} graded records from {args.merged}", flush=True)

    exam_id = _find_or_create_exam(args.exam_name)
    print(f"Exam: {args.exam_name!r} ({exam_id})", flush=True)

    questions = _build_question_bank(results)
    # The exam's review threshold is part of its configuration; take the one the
    # grading run used so the imported exam flags exactly what the eval flagged.
    review_threshold = (payload.get("summary") or {}).get("review_confidence_threshold")
    update_exam(exam_id, questions=questions, review_confidence_threshold=review_threshold)
    print(
        f"Answer key set: {len(questions)} questions"
        + (f", review threshold {review_threshold}" if review_threshold is not None else ""),
        flush=True,
    )

    by_student: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_student.setdefault(r["student_id"], []).append(r)

    with get_db() as conn:
        for student_id, records in sorted(
            by_student.items(), key=lambda kv: int(kv[0].split("_")[1])
        ):
            script_id = f"mendeley-{student_id}"
            pdf_path = STUDENT_PDF_DIR / f"{student_id}.pdf"

            conn.execute(
                "INSERT OR IGNORE INTO students(student_id, name) VALUES (?, ?)",
                (student_id, student_id.replace("_", " ")),
            )
            conn.execute(
                """
                INSERT INTO scripts(script_id, student_id, filename, file_path, status, exam_id)
                VALUES (?, ?, ?, ?, 'processed', ?)
                ON CONFLICT(script_id) DO UPDATE SET exam_id=excluded.exam_id
                """,
                (script_id, student_id, f"{student_id}.pdf", str(pdf_path), exam_id),
            )

            for r in records:
                qid = r["question_id"]
                is_mcq = r["question_type"] == "MCQ"
                answer_text = r.get("extracted_answer", "")

                # The script-detail / review-queue views read the student's answer
                # text from question_extractions, not question_evaluations — populate both.
                conn.execute(
                    """
                    INSERT INTO question_extractions
                        (extraction_id, script_id, question_id, source_path, page_number,
                         extracted_text, extraction_method)
                    VALUES (?, ?, ?, NULL, NULL, ?, 'mendeley_offline_batch')
                    ON CONFLICT(script_id, question_id) DO UPDATE SET
                        extracted_text=excluded.extracted_text,
                        extraction_method=excluded.extraction_method
                    """,
                    (f"{script_id}:{qid}:extraction", script_id, qid, answer_text),
                )

                reasoning = r.get("reasoning") or (r.get("review_reason", "") if is_mcq else "")
                evidence = r.get("evidence") or (
                    [r["review_reason"]] if is_mcq and r.get("review_reason") else []
                )

                conn.execute(
                    """
                    INSERT INTO question_evaluations
                        (eval_id, script_id, question_id, question_text, golden_answer, rubric_json,
                         max_marks, awarded_marks, confidence, needs_review, status,
                         evidence_json, reasoning, llm_model, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(script_id, question_id) DO UPDATE SET
                        question_text=excluded.question_text,
                        golden_answer=excluded.golden_answer,
                        rubric_json=excluded.rubric_json,
                        max_marks=excluded.max_marks,
                        awarded_marks=excluded.awarded_marks,
                        confidence=excluded.confidence,
                        needs_review=excluded.needs_review,
                        status=excluded.status,
                        evidence_json=excluded.evidence_json,
                        reasoning=excluded.reasoning,
                        llm_model=excluded.llm_model,
                        updated_at=datetime('now')
                    """,
                    (
                        f"{script_id}:{qid}",
                        script_id,
                        qid,
                        r.get("question_text", ""),
                        r.get("golden_answer", ""),
                        json.dumps(r.get("rubric", [])),
                        r["max_marks"],
                        r["awarded_marks"],
                        r["confidence"],
                        int(bool(r["needs_review"])),
                        r["status"],
                        json.dumps(evidence),
                        reasoning,
                        r.get("llm_model"),
                    ),
                )

            print(f"  {student_id}: {len(records)} questions imported", flush=True)

    print(
        f"\nDone. Open the app -> Exams -> {args.exam_name!r} to see all 50 students.", flush=True
    )


if __name__ == "__main__":
    main()
