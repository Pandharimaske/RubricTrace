#!/usr/bin/env python3
"""
Quick single-script diagnostic for the VLM extraction step — run this on ONE
student before kicking off a full batch run, so you're not waiting through
50 students to discover extraction is still broken.

Usage:
    python scripts/diagnose_extraction.py Student_1
    python scripts/diagnose_extraction.py Student_1 --questions Q21,Q22,Q23
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.extraction.reader import ScriptReader

ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = (
    ROOT
    / "data/raw/mendeley_sf3kvjwknt/archive"
    / "A Dataset of Digitized Student Examination Papers,"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose VLM extraction on one student script.")
    parser.add_argument("student_id", help="e.g. Student_1")
    parser.add_argument(
        "--questions",
        help="Comma-separated question ID hint list, e.g. Q21,Q22,Q23. "
        "Defaults to all 35 (Q1-Q35).",
    )
    args = parser.parse_args()

    script_path = DATASET_ROOT / "Student_Pdf" / f"{args.student_id}.pdf"
    if not script_path.exists():
        raise SystemExit(f"No such script: {script_path}")

    question_ids = (
        [q.strip() for q in args.questions.split(",")]
        if args.questions
        else [f"Q{i}" for i in range(1, 36)]
    )

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"Extracting {script_path.name} with hint IDs: {question_ids}\n")
    result = ScriptReader().read(
        script_path,
        ROOT / "data/processed/diagnose" / args.student_id,
        question_ids=question_ids,
    )

    print(f"Method: {result.method}  |  Model: {result.vlm_model}  |  Pages: {result.page_count}\n")
    questions = result.questions
    if not questions:
        print("!! No questions were extracted at all. Check NVIDIA_API_KEY and the vision model.")
        return

    empty = [qid for qid, text in questions.items() if not text.strip()]
    filled = {qid: text for qid, text in questions.items() if text.strip()}

    print(
        f"Extracted {len(questions)} question IDs total — {len(filled)} with text, {len(empty)} empty.\n"
    )
    print("--- Filled answers ---")
    for qid in sorted(filled, key=lambda q: (len(q), q)):
        preview = filled[qid][:150].replace("\n", " ")
        print(f"  {qid}: {preview}{'…' if len(filled[qid]) > 150 else ''}")

    if empty:
        print("\n--- Empty (nothing extracted) ---")
        print(f"  {sorted(empty, key=lambda q: (len(q), q))}")

    conflicts = result.conflicts
    if conflicts:
        print(f"\n--- Flagged for manual review ({len(conflicts)}) ---")
        for item in conflicts:
            key = item.get("conflict_key") or item.get("question_id") or "?"
            print(f"  [{key}] {item.get('message', '')}")
            suggestion = item.get("suggested_question_id")
            if suggestion:
                print(f"      → suggested question: {suggestion} (confirm manually)")


if __name__ == "__main__":
    main()
