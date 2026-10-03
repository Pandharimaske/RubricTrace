#!/usr/bin/env python3
"""
Import exam structure, questions, rubrics, student PDFs, and extractions
without running grading. This populates the database with the Claude-extracted
answers so you can view them in the frontend.

Usage:
    python scripts/import_exam_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from uuid import uuid4

import psycopg
from backend.app.core.settings import DATABASE_URL, PROCESSED_DIR, PROJECT_ROOT, RAW_DIR
from psycopg.rows import dict_row

STUDENT_PDF_DIR = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "mendeley_sf3kvjwknt"
    / "archive"
    / "A Dataset of Digitized Student Examination Papers,"
    / "Student_Pdf"
)

EXAM_NAME = "Data Science Exam — 50 Students"


def _find_or_create_exam(name: str) -> str:
    with get_db() as conn:
        # Check if exam exists
        existing = conn.execute("SELECT exam_id FROM exams WHERE name=%s", (name,)).fetchone()

        if existing:
            exam_id = existing["exam_id"]
            # Delete existing exam data
            conn.execute(
                "DELETE FROM question_extractions WHERE script_id IN (SELECT script_id FROM scripts WHERE exam_id=%s)",
                (exam_id,),
            )
            conn.execute(
                "DELETE FROM question_evaluations WHERE script_id IN (SELECT script_id FROM scripts WHERE exam_id=%s)",
                (exam_id,),
            )
            conn.execute("DELETE FROM scripts WHERE exam_id=%s", (exam_id,))
            conn.execute("DELETE FROM exam_questions WHERE exam_id=%s", (exam_id,))
            conn.execute("DELETE FROM exams WHERE exam_id=%s", (exam_id,))
            print(f"Deleted existing exam {exam_id}")

    # Always create a new exam
    exam_id = uuid4().hex
    with get_db() as conn:
        conn.execute("INSERT INTO exams(exam_id, name) VALUES (%s, %s)", (exam_id, name))
    return exam_id


def _load_exam_rubric() -> dict[str, Any]:
    rubric_path = RAW_DIR / "exam_rubric.json"
    return json.loads(rubric_path.read_text(encoding="utf-8"))


def _build_questions_from_rubric(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert exam_rubric.json to the question format expected by the database."""
    questions = []

    for section in rubric.get("sections", []):
        section_type = section.get("type", "short_answer")
        for q in section.get("questions", []):
            qid = f"Q{q['id']}"

            # Build criteria from rubric
            criteria = []
            for criterion in q.get("rubric", []):
                criteria.append(
                    {
                        "name": criterion.get("criterion", ""),
                        "description": criterion.get("description", ""),
                        "marks": criterion.get("points", 0),
                        "expected_concepts": criterion.get("expected_concepts", []),
                        "guidance": criterion.get("guidance", ""),
                    }
                )

            # Build options for MCQ
            options = []
            if section_type == "mcq":
                for letter, text in q.get("options", {}).items():
                    is_correct = letter.upper() == q.get("correct_answer", "").upper()
                    options.append(
                        {
                            "option_key": letter.upper(),
                            "option_text": text,
                            "is_correct": is_correct,
                            "display_order": len(options),
                        }
                    )

            question = {
                "question_id": qid,
                "question_text": q.get("question", ""),
                "question_type": "mcq" if section_type == "mcq" else "short_answer",
                "golden_answer": q.get("correct_answer", "")
                if section_type == "mcq"
                else q.get("model_answer", ""),
                "max_marks": float(q.get("max_marks", 0)),
                "criteria": criteria,
                "options": options,
            }
            questions.append(question)

    return questions


def main() -> None:
    # Load the extracted answers index
    extracted_path = PROCESSED_DIR / "extracted_answers_index.json"
    if not extracted_path.exists():
        print(f"Error: {extracted_path} not found. Run build_extracted_answers_index.py first.")
        sys.exit(1)

    extracted_payload = json.loads(extracted_path.read_text(encoding="utf-8"))
    extracted_records = extracted_payload["results"]
    print(f"Loaded {len(extracted_records)} extraction records from {extracted_path}")

    # Load exam rubric
    rubric = _load_exam_rubric()
    print(f"Loaded exam rubric with {len(rubric.get('sections', []))} sections")

    # Use direct psycopg connection to avoid prepared statement caching issues
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
    # Disable prepared statements to avoid caching issues
    conn.execute("SET statement_timeout = 300000")  # 5 minute timeout

    try:
        # Create or find exam
        existing = conn.execute(
            "SELECT exam_id FROM exams WHERE name=%s", (EXAM_NAME,), prepare=False
        ).fetchone()

        if existing:
            exam_id = existing["exam_id"]
            # Delete existing exam data
            conn.execute(
                "DELETE FROM question_extractions WHERE script_id IN (SELECT script_id FROM scripts WHERE exam_id=%s)",
                (exam_id,),
                prepare=False,
            )
            conn.execute(
                "DELETE FROM question_evaluations WHERE script_id IN (SELECT script_id FROM scripts WHERE exam_id=%s)",
                (exam_id,),
                prepare=False,
            )
            conn.execute("DELETE FROM scripts WHERE exam_id=%s", (exam_id,), prepare=False)
            conn.execute("DELETE FROM exam_questions WHERE exam_id=%s", (exam_id,), prepare=False)
            conn.execute("DELETE FROM exams WHERE exam_id=%s", (exam_id,), prepare=False)
            print(f"Deleted existing exam {exam_id}")

        exam_id = uuid4().hex
        conn.execute(
            "INSERT INTO exams(exam_id, name) VALUES (%s, %s)", (exam_id, EXAM_NAME), prepare=False
        )
        print(f"Exam: {EXAM_NAME!r} ({exam_id})")

        # Build and set questions
        questions = _build_questions_from_rubric(rubric)
        review_threshold = rubric.get("review_confidence_threshold", 0.65)

        conn.execute(
            "UPDATE exams SET review_confidence_threshold=%s WHERE exam_id=%s",
            (review_threshold, exam_id),
            prepare=False,
        )

        for number, question in enumerate(questions, start=1):
            question_row_id = uuid4().hex
            conn.execute(
                """
                INSERT INTO exam_questions
                    (question_row_id, exam_id, question_id, question_number, question_text,
                     question_type, golden_answer, max_marks, review_confidence_threshold,
                     evaluator_config_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                """,
                (
                    question_row_id,
                    exam_id,
                    question["question_id"],
                    number,
                    question.get("question_text", ""),
                    question["question_type"],
                    question.get("golden_answer", ""),
                    question["max_marks"],
                    None,
                ),
                prepare=False,
            )

            for index, criterion in enumerate(question.get("criteria", [])):
                criterion_id = uuid4().hex
                conn.execute(
                    """
                    INSERT INTO rubric_criteria
                        (criterion_id, question_row_id, name, description, max_marks,
                         expected_concepts, guidance, display_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        criterion_id,
                        question_row_id,
                        criterion["name"],
                        criterion.get("description", ""),
                        criterion["marks"],
                        json.dumps(criterion.get("expected_concepts", [])),
                        criterion.get("guidance", ""),
                        index,
                    ),
                    prepare=False,
                )

            for index, option in enumerate(question.get("options", [])):
                option_id = uuid4().hex
                conn.execute(
                    """
                    INSERT INTO question_options
                        (option_id, question_row_id, option_key, option_text, is_correct, display_order)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        option_id,
                        question_row_id,
                        option["option_key"],
                        option["option_text"],
                        int(option.get("is_correct", False)),
                        option.get("display_order", index),
                    ),
                    prepare=False,
                )

        print(f"Set {len(questions)} questions in exam with review threshold {review_threshold}")

        # Group extractions by student
        by_student: dict[str, list[dict[str, Any]]] = {}
        for r in extracted_records:
            by_student.setdefault(r["student_id"], []).append(r)

        # Import students, scripts, and extractions
        for student_id, records in sorted(
            by_student.items(), key=lambda kv: int(kv[0].split("_")[1])
        ):
            script_id = f"mendeley-{student_id}"
            pdf_path = STUDENT_PDF_DIR / f"{student_id}.pdf"

            # Create student
            conn.execute(
                "INSERT INTO students(student_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (student_id, student_id.replace("_", " ")),
                prepare=False,
            )

            # Create script
            existing = conn.execute(
                "SELECT script_id FROM scripts WHERE script_id=%s", (script_id,), prepare=False
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE scripts SET
                        student_id=%s,
                        filename=%s,
                        file_path=%s,
                        status='processed',
                        exam_id=%s
                    WHERE script_id=%s
                    """,
                    (student_id, f"{student_id}.pdf", str(pdf_path), exam_id, script_id),
                    prepare=False,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO scripts(script_id, student_id, filename, file_path, status, exam_id)
                    VALUES (%s, %s, %s, %s, 'processed', %s)
                    """,
                    (script_id, student_id, f"{student_id}.pdf", str(pdf_path), exam_id),
                    prepare=False,
                )

            # Delete existing extractions for this script first
            conn.execute(
                "DELETE FROM question_extractions WHERE script_id=%s",
                (script_id,),
                prepare=False,
            )

            # Insert extractions one by one
            for r in records:
                qid = r["question_id"]
                answer_text = r.get("extracted_answer", "")
                page_number = r.get("page_number")
                extraction_id = f"{script_id}:{qid}:extraction"

                conn.execute(
                    """
                    INSERT INTO question_extractions
                        (extraction_id, script_id, question_id, question_number, question_type, source_path,
                         page_number, extracted_text, extraction_method, ocr_confidence, vlm_confidence,
                         extraction_confidence, needs_review, review_reason)
                    VALUES (%s, %s, %s, NULL, 'unknown', NULL, %s, %s, %s, NULL, NULL, NULL, 0, '')
                    """,
                    (extraction_id, script_id, qid, page_number, answer_text, "claude_vlm"),
                    prepare=False,
                )

            print(f"  {student_id}: {len(records)} extractions imported")

        conn.commit()
        print(f"\nDone. Open the app -> Exams -> {EXAM_NAME!r} to see all 50 students.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
