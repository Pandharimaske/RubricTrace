#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.grading.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RubricTrace batch evaluation pipeline.")
    parser.add_argument("metadata", type=Path, help="CSV or JSON dataset metadata file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/processed/pipeline_results.json",
        help="JSON output path",
    )
    parser.add_argument(
        "--student",
        action="append",
        help="Only grade this student (e.g. --student Student_1). Repeat the flag "
        "for more than one. Use this for a quick validation run before the full batch.",
    )
    args = parser.parse_args()
    payload = run_pipeline(args.metadata, args.output, student_ids=args.student)
    print(f"Wrote {args.output}")
    print(payload["summary"])


if __name__ == "__main__":
    main()
