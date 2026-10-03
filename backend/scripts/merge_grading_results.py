#!/usr/bin/env python3
"""
Merge the two parallel grading tracks (grade_mcq_local.py's results_mcq.json
and grade_short_answer_cloud.py's results_short_answer.json) into the single
results file compute_grading_metrics.py expects: {"summary": {...},
"results": [...]}.

Why this is its own step rather than each grader writing to a shared file
directly: the two graders run as separate OS processes (see
run_grading_parallel.sh) against different output files, so there's no shared
file lock to fight over and each track can be resumed/re-run independently.
Merging is a cheap, idempotent, read-only-on-the-inputs final step.

Input
-----
--mcq            results_mcq.json from grade_mcq_local.py.
                  Defaults to data/processed/results_mcq.json.
--short-answer   results_short_answer.json from grade_short_answer_cloud.py.
                  Defaults to data/processed/results_short_answer.json.

Output
------
--output         Combined {"summary":..., "results":[...]} JSON, ready for
                  compute_grading_metrics.py. Defaults to
                  data/processed/results_merged.json.

Edge cases handled explicitly:
  - either input file missing or empty: aborts with a clear error naming
    which track is missing, rather than silently producing a partial report
    (use --allow-partial to merge whatever is present instead, e.g. while one
    track is still running)
  - a (student_id, question_id) key present in BOTH input files (a manifest
    overlap bug, since MCQ and short-answer question IDs should be disjoint):
    reported as a warning listing every colliding key; the short-answer
    record wins (arbitrary but deterministic) and the run continues
  - a record missing question_type (older output shape): backfilled from
    which input file it came from ("MCQ" / "short_answer") before merging
  - duplicate keys WITHIN a single input file (a track was interrupted and
    resumed oddly): last one in the file wins, matching how the graders'
    own resume-by-key logic already treats their output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.settings import PROCESSED_DIR


def _load_results(path: Path, default_question_type: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("results", payload) if isinstance(payload, dict) else payload

    # De-dupe within this one file, last-write-wins, keyed the same way the
    # graders themselves resume by (student_id, question_id).
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:
        key = (str(r.get("student_id", "")), str(r.get("question_id", "")))
        if not r.get("question_type"):
            r = {**r, "question_type": default_question_type}
        by_key[key] = r
    return list(by_key.values())


def _load_threshold(path: Path) -> float | None:
    """The review threshold a grading run recorded in its summary, if any."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        value = (payload.get("summary") or {}).get("review_confidence_threshold")
        return float(value) if value is not None else None
    return None


def merge(
    mcq_records: list[dict[str, Any]], short_answer_records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    mcq_by_key = {(r["student_id"], r["question_id"]): r for r in mcq_records}
    sa_by_key = {(r["student_id"], r["question_id"]): r for r in short_answer_records}

    collisions = sorted(set(mcq_by_key) & set(sa_by_key))

    merged_by_key: dict[tuple[str, str], dict[str, Any]] = dict(mcq_by_key)
    merged_by_key.update(sa_by_key)  # short-answer wins on collision, see docstring

    # Sort for a stable, diffable output file: student_id, then question_id
    # numerically where possible (Q2 before Q10), falling back to lexical.
    def _qsort(qid: str) -> tuple[int, str]:
        digits = "".join(ch for ch in qid if ch.isdigit())
        return (int(digits), qid) if digits else (10**9, qid)

    ordered_keys = sorted(merged_by_key, key=lambda k: (k[0], _qsort(k[1])))
    merged = [merged_by_key[k] for k in ordered_keys]
    return merged, collisions


def _build_summary(
    records: list[dict[str, Any]], review_threshold: float | None = None
) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_type.setdefault(r.get("question_type", "unknown"), []).append(r)

    def _group_summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        evaluated = [r for r in group if r.get("absolute_error") is not None]
        return {
            "records": len(group),
            "scored": sum(r.get("status") == "scored" for r in group),
            "needs_review": sum(r.get("status") == "needs_review" for r in group),
            "manual_mark_records": len(evaluated),
            "mae": (
                round(sum(r["absolute_error"] for r in evaluated) / len(evaluated), 4)
                if evaluated
                else None
            ),
        }

    summary: dict[str, Any] = {"total_records": len(records)}
    if review_threshold is not None:
        summary["review_confidence_threshold"] = review_threshold
    for qtype, group in sorted(by_type.items()):
        summary[qtype] = _group_summary(group)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mcq", type=Path, default=PROCESSED_DIR / "results_mcq.json")
    parser.add_argument(
        "--short-answer", type=Path, default=PROCESSED_DIR / "results_short_answer.json"
    )
    parser.add_argument("--output", type=Path, default=PROCESSED_DIR / "results_merged.json")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Merge whatever input file(s) are present instead of aborting if one is missing "
        "(e.g. to inspect progress while the other track is still running).",
    )
    args = parser.parse_args()

    mcq_missing = not args.mcq.exists()
    sa_missing = not args.short_answer.exists()

    if (mcq_missing or sa_missing) and not args.allow_partial:
        missing = []
        if mcq_missing:
            missing.append(f"MCQ track: {args.mcq}")
        if sa_missing:
            missing.append(f"short-answer track: {args.short_answer}")
        raise SystemExit(
            "Missing input file(s), refusing to merge a partial report:\n  "
            + "\n  ".join(missing)
            + "\nRun the missing grading script first, or pass --allow-partial "
            "to merge whatever is present."
        )

    mcq_records = _load_results(args.mcq, "MCQ") if args.mcq.exists() else []
    sa_records = (
        _load_results(args.short_answer, "short_answer") if args.short_answer.exists() else []
    )

    if not mcq_records and not sa_records:
        raise SystemExit("Both input files are empty or missing — nothing to merge.")

    merged, collisions = merge(mcq_records, sa_records)

    # Both tracks should have been graded under the same exam threshold; if they
    # weren't, needs_review counts aren't comparable, so say so loudly.
    thresholds = {
        name: t
        for name, t in (
            ("mcq", _load_threshold(args.mcq) if args.mcq.exists() else None),
            (
                "short_answer",
                _load_threshold(args.short_answer) if args.short_answer.exists() else None,
            ),
        )
        if t is not None
    }
    if len(set(thresholds.values())) > 1:
        print(
            f"WARNING: the two tracks used different review thresholds: {thresholds}. "
            "Recorded the short-answer value.",
            flush=True,
        )
    review_threshold = thresholds.get("short_answer", thresholds.get("mcq"))

    if collisions:
        print(
            f"WARNING: {len(collisions)} (student_id, question_id) key(s) appear in BOTH "
            "tracks — this shouldn't happen if the MCQ and short-answer manifests are "
            "disjoint. Short-answer record kept for each. Colliding keys:",
            flush=True,
        )
        for student_id, question_id in collisions:
            print(f"  {student_id} {question_id}", flush=True)

    summary = _build_summary(merged, review_threshold)
    payload = {"summary": summary, "results": merged}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        f"Merged {len(mcq_records)} MCQ + {len(sa_records)} short-answer record(s) "
        f"-> {len(merged)} total.",
        flush=True,
    )
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
