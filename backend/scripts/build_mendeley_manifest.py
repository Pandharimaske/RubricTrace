#!/usr/bin/env python3
"""
Build JSON manifests for the RubricTrace batch pipeline (scripts/run_pipeline.py
and scripts/grade_short_answer_cloud.py / grade_mcq_local.py) from two sources:

  - data/raw/exam_rubric.json                                     (teacher-
    authored: question text, options, correct MCQ answers, model short-answer
    answers, and PER-CRITERION short-answer rubrics with their own point
    values — this is the ground truth for what each question asks and how it
    should be graded)
  - data/raw/mendeley_sf3kvjwknt/archive/.../Teacher_manual_marks_Anonymized.csv
    and .../Student_Pdf/Student_<n>.pdf                            (per-
    student ground-truth marks/choices for MAE, and script paths to feed into
    the extraction pipeline)

Previously this script derived short-answer rubrics itself by regex-splitting
the golden-answer prose into fragments (one "expected_concepts" list per
question, via a since-removed _concepts() helper). That produced checklist-
shaped rubrics with no way to distinguish "these N items are all required" from
"these are example answers, pick any two" — which the grader would then
literalize into all-or-nothing or proportional-coverage scoring even on
questions phrased "list any two...". exam_rubric.json's rubric is
teacher-written per criterion (e.g. Q25 "list any two applications" has two
independent 1-point criteria, each "gives one valid application" / "a second,
distinct valid application" — correctly modeling the any-two semantics
instead of requiring every listed example). Use that instead of re-deriving
it from prose.

Writes three manifests to data/processed/, each matching the DatasetRecord/JSON
schema `load_dataset()` expects (student_id, script_path, question_id,
question_text, golden_answer, max_marks, manual_marks, criteria):

  - mendeley_manifest_short_answer.json  Q21-Q35 (2 marks each) - the actual
    descriptive-grading use case RubricTrace is built for. Use this one for
    the headline MAE metric.
  - mendeley_manifest_mcq.json            Q1-Q20 (1 mark each) - objective,
    included for completeness/comparison.
  - mendeley_manifest_full.json           all 35 questions combined.

Usage:
    python scripts/build_mendeley_manifest.py

Then, for each manifest you want metrics on:
    python scripts/run_pipeline.py data/processed/mendeley_manifest_short_answer.json \
        --output data/processed/mendeley_results_short_answer.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from backend.app.db.database import DEFAULT_REVIEW_THRESHOLD, clamp_review_threshold  # noqa: E402

DATASET_ROOT = (
    ROOT
    / "data/raw/mendeley_sf3kvjwknt/archive"
    / "A Dataset of Digitized Student Examination Papers,"
)
RUBRIC_PATH = ROOT / "data/raw/exam_rubric.json"
OUTPUT_DIR = ROOT / "data/processed"


def _format_mcq_question_text(q: dict[str, Any]) -> str:
    """Matches the previous Question.txt-parsed shape: question line, then one
    "<letter>. <text>" line per option, so downstream prompts/grading that
    expect an inline A-D option list keep working unchanged."""
    lines = [q["question"]]
    for letter, text in q["options"].items():
        lines.append(f"{letter}. {text}")
    return "\n".join(lines)


def _load_rubric(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Returns (mcq_by_id, short_answer_by_id), each keyed by 'Q<n>'."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    mcq: dict[str, dict[str, Any]] = {}
    short_answer: dict[str, dict[str, Any]] = {}
    for section in payload["sections"]:
        target = mcq if section["type"] == "mcq" else short_answer
        for q in section["questions"]:
            target[f"Q{q['id']}"] = q
    return mcq, short_answer


def _load_review_threshold(path: Path) -> float:
    """The exam's review_confidence_threshold (exam_rubric.json), or the default."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return clamp_review_threshold(
        payload.get("review_confidence_threshold", DEFAULT_REVIEW_THRESHOLD)
    )


def _manual_mark(raw: str, question_type: str, expected: str) -> float | None:
    if question_type == "MCQ":
        return 1.0 if raw.strip().upper() == expected.strip().upper() else 0.0
    if raw.strip().upper() == "NC":
        return 0.0
    if raw.strip().upper() in {"", "-", "NM"}:
        return None
    return float(raw)


def build(notes_path: Path | None = None, suffix: str = "") -> None:
    # Optional per-question grading notes (e.g. data/raw/grading_notes_v2.json), keyed
    # 'Q22' etc. They travel with each short-answer record as 'guidance' and are added
    # to the grader prompt by grade_short_answer_cloud.py. exam_rubric.json is never
    # modified, so the original-rubric run stays reproducible.
    notes: dict[str, str] = {}
    if notes_path is not None:
        notes = {
            k: v
            for k, v in json.loads(notes_path.read_text(encoding="utf-8")).items()
            if not k.startswith("_")
        }
        print(f"Loaded grading notes for {sorted(notes)} from {notes_path}")
    mcq_questions, sa_questions = _load_rubric(RUBRIC_PATH)
    review_threshold = _load_review_threshold(RUBRIC_PATH)
    marks_path = DATASET_ROOT / "Teacher_manual_marks_Anonymized.csv"
    student_pdf_dir = DATASET_ROOT / "Student_Pdf"

    all_records: list[dict] = []
    mcq_records: list[dict] = []
    sa_records: list[dict] = []
    missing_pdfs: list[str] = []

    all_questions = {**mcq_questions, **sa_questions}

    with marks_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            student_id = row["Student_ID"]
            script_path = student_pdf_dir / f"{student_id}.pdf"
            if not script_path.exists():
                missing_pdfs.append(student_id)
                continue

            for question_id, q in all_questions.items():
                is_mcq = question_id in mcq_questions
                col = question_id[1:]  # "Q1" -> "1"
                max_marks = float(q["max_marks"])

                if is_mcq:
                    golden_answer = q["correct_answer"].strip().upper()
                    manual_marks = _manual_mark(row[col], "MCQ", golden_answer)
                    qtext = _format_mcq_question_text(q)
                    criteria = [
                        {
                            "criterion": "correct_option",
                            "points": max_marks,
                            "guidance": (
                                f"The correct option is {golden_answer}. Students wrote their answer "
                                "in inconsistent formats — some as just the letter (e.g. 'C'), some "
                                "as the question number plus letter (e.g. '1. C'), some as letter plus "
                                "the option's own text (e.g. 'C. Little or no autocorrelation'), and "
                                "some as only the option's text with no letter at all. Read the question "
                                "text above (it lists options A-D) and judge what the student meant by "
                                "meaning, not by exact string match against the letter. Award full marks "
                                f"if the student's answer indicates option {golden_answer} in any of "
                                "these forms, and zero marks otherwise. This is an objective "
                                "multiple-choice question; there is no partial credit."
                            ),
                        }
                    ]
                else:
                    golden_answer = q["model_answer"].strip()
                    manual_marks = _manual_mark(row[col], "short_answer", "")
                    qtext = q["question"]
                    # Pass the teacher-authored per-criterion rubric straight through —
                    # each entry already carries its own point value and a
                    # criterion description written to allow "any valid answer
                    # of this kind" (e.g. "a second, distinct valid
                    # application") rather than requiring a fixed set of
                    # phrases, so no concept-fragmenting is needed here.
                    criteria = [
                        {
                            "criterion": c["criterion"],
                            "points": c["points"],
                        }
                        for c in q["rubric"]
                    ]

                record = {
                    "student_id": student_id,
                    "script_path": str(script_path.resolve()),
                    "question_id": question_id,
                    "question_text": qtext,
                    "golden_answer": golden_answer,
                    "question_type": "MCQ" if is_mcq else "short_answer",
                    "max_marks": max_marks,
                    "manual_marks": manual_marks,
                    "criteria": criteria,
                }
                if not is_mcq and question_id in notes:
                    record["guidance"] = notes[question_id]
                all_records.append(record)
                (mcq_records if is_mcq else sa_records).append(record)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, records in (
        (f"mendeley_manifest_full{suffix}.json", all_records),
        (f"mendeley_manifest_mcq{suffix}.json", mcq_records),
        (f"mendeley_manifest_short_answer{suffix}.json", sa_records),
    ):
        out_path = OUTPUT_DIR / name
        # The threshold travels with the manifest so the graders judge needs_review
        # against the exam's own setting (not a constant baked into the grader).
        out_path.write_text(
            json.dumps(
                {"review_confidence_threshold": review_threshold, "records": records}, indent=2
            ),
            encoding="utf-8",
        )
        print(f"Wrote {len(records)} records to {out_path} (review threshold {review_threshold})")

    if missing_pdfs:
        print(f"WARNING: no PDF found for {len(missing_pdfs)} students: {missing_pdfs}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--notes",
        type=Path,
        default=None,
        help="JSON of per-question grading notes (e.g. data/raw/grading_notes_v2.json).",
    )
    ap.add_argument(
        "--suffix",
        default="",
        help="Suffix for output manifests, e.g. _v2, so existing manifests are not overwritten.",
    )
    cli = ap.parse_args()
    build(cli.notes, cli.suffix)
