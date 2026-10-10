#!/usr/bin/env python3
"""
Print a handful of full graded records (extracted answer, rubric reasoning,
awarded vs manual) for specific question_ids, to diagnose why they show
unusually high bias/MAE. Prints the biggest misses first.

Usage:
    python scripts/inspect_question.py Q22 Q23 Q27 Q29 --n 4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question_ids", nargs="+")
    parser.add_argument("--n", type=int, default=4, help="Records to show per question_id")
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/processed/results_short_answer.json",
    )
    args = parser.parse_args()

    payload = json.loads(args.results.read_text(encoding="utf-8"))
    results = payload.get("results", payload)

    for qid in args.question_ids:
        recs = [r for r in results if r["question_id"] == qid and r.get("manual_marks") is not None]
        recs.sort(
            key=lambda r: abs(float(r["awarded_marks"]) - float(r["manual_marks"])), reverse=True
        )
        print(
            f"\n{'=' * 70}\n{qid}  ({len(recs)} graded records, showing top {args.n} misses)\n{'=' * 70}"
        )
        for r in recs[: args.n]:
            print(f"\n--- {r['student_id']} ---")
            print(f"extracted_answer: {r.get('extracted_answer', '')!r}")
            print(
                f"awarded={r['awarded_marks']} manual={r['manual_marks']} confidence={r.get('confidence')} needs_review={r.get('needs_review')}"
            )
            print(f"reasoning: {r.get('reasoning', r.get('review_reason', ''))!r}")
            if "concepts_missing" in r:
                print(f"concepts_missing: {r['concepts_missing']}")


if __name__ == "__main__":
    main()
