#!/usr/bin/env python3
"""
Backfill page_number in question_extractions and question_evaluations from
data/processed/extracted_answers_index.json -- the VLM extraction index that
actually carries page_number per (student_id, question_id). Whatever import
populated the DB's question_extractions/question_evaluations rows from the grading
results (results_mcq.json / results_short_answer.json) never carried
page_number through (those files don't have it), so it's NULL for every
imported row today -- this is why PageSlider defaults to page 1 for every
question instead of jumping to the page it was actually extracted from.

Maps Student_<N> -> script_id "mendeley-Student_<N>" (the convention already
used by the DB rows, confirmed via `SELECT script_id FROM scripts`).

Usage:
    python scripts/backfill_page_numbers.py
    python scripts/backfill_page_numbers.py --force   # overwrite existing non-null values too
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.settings import PROCESSED_DIR
from backend.app.db.database import get_db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--index",
        type=Path,
        default=PROCESSED_DIR / "extracted_answers_index.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite page_number even where a non-null value already exists",
    )
    args = parser.parse_args()

    payload = json.loads(args.index.read_text(encoding="utf-8"))
    records = payload.get("results", payload)

    updated_extractions = 0
    updated_evals = 0
    no_page = 0
    not_found_extractions = 0
    not_found_evals = 0

    with get_db() as conn:
        for r in records:
            page_number = r.get("page_number")
            if page_number is None:
                no_page += 1
                continue

            script_id = f"mendeley-{r['student_id']}"
            question_id = r["question_id"]

            where_clause = (
                "script_id=? AND question_id=?"
                if args.force
                else "script_id=? AND question_id=? AND page_number IS NULL"
            )

            cur = conn.execute(
                f"UPDATE question_extractions SET page_number=? WHERE {where_clause}",
                (page_number, script_id, question_id),
            )
            if cur.rowcount:
                updated_extractions += cur.rowcount
            else:
                not_found_extractions += 1

            cur = conn.execute(
                f"UPDATE question_evaluations SET page_number=? WHERE {where_clause}",
                (page_number, script_id, question_id),
            )
            if cur.rowcount:
                updated_evals += cur.rowcount
            else:
                not_found_evals += 1

    print(f"question_extractions updated:  {updated_extractions}")
    print(f"question_evaluations updated: {updated_evals}")
    print(f"records with no page_number in index (skipped): {no_page}")
    print(
        f"extraction rows not matched (no row, or already had a value without --force): {not_found_extractions}"
    )
    print(f"evaluations rows not matched (same as above): {not_found_evals}")


if __name__ == "__main__":
    main()
