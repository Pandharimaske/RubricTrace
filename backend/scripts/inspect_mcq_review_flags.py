#!/usr/bin/env python3
"""
Pull every MCQ record flagged needs_review=true and show whether the flag was
actually warranted (awarded_marks != manual_marks) or a false positive (score
was correct, but review was triggered anyway) — plus buckets the review_reason
text so we can see which failure mode dominates the 188 flags.

Usage:
    python scripts/inspect_mcq_review_flags.py
    python scripts/inspect_mcq_review_flags.py --show-examples 8
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def bucket(reason: str) -> str:
    r = reason.lower()
    if (
        "does not match any" in r
        or "doesn't match any" in r
        or "not one of" in r
        or "invalid option" in r
    ):
        return "reason claims answer isn't a valid option"
    if "ambig" in r or "more than one" in r or "multiple" in r:
        return "genuinely ambiguous / multiple marked"
    if "blank" in r or "empty" in r or "no answer" in r:
        return "blank / no answer"
    if "illegible" in r or "unclear" in r or "unreadable" in r:
        return "illegible transcription"
    return "other"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-examples", type=int, default=5, help="Examples to print per bucket")
    parser.add_argument(
        "--reasons-only",
        action="store_true",
        help="Skip the bucket breakdown -- just print every flagged record's "
        "student/question/answer/reason, one per line, sorted by question_id.",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/processed/results_mcq.json",
    )
    args = parser.parse_args()

    payload = json.loads(args.results.read_text(encoding="utf-8"))
    results = payload.get("results", payload)

    flagged = [r for r in results if r.get("needs_review")]

    if args.reasons_only:
        flagged.sort(key=lambda r: (r["question_id"], r["student_id"]))
        for r in flagged:
            fp = r.get("manual_marks") is not None and float(r["awarded_marks"]) == float(
                r["manual_marks"]
            )
            tag = "FP" if fp else "real"
            print(
                f"[{tag}] {r['student_id']:<12} {r['question_id']:<5} "
                f"answer={r.get('extracted_answer', '')!r:<12} "
                f"golden={r.get('golden_answer', '')!r:<6} "
                f"conf={r.get('confidence')}  reasoning={(r.get('reasoning') or r.get('review_reason', ''))!r}"
            )
        print(f"\nTotal flagged: {len(flagged)}")
        return
    correct_score_flagged = [
        r
        for r in flagged
        if r.get("manual_marks") is not None
        and float(r["awarded_marks"]) == float(r["manual_marks"])
    ]
    wrong_score_flagged = [
        r
        for r in flagged
        if r.get("manual_marks") is not None
        and float(r["awarded_marks"]) != float(r["manual_marks"])
    ]

    print(f"Total flagged needs_review: {len(flagged)}")
    print(f"  -> score was actually CORRECT (false-positive flag): {len(correct_score_flagged)}")
    print(f"  -> score was actually WRONG (true-positive flag):    {len(wrong_score_flagged)}")

    buckets: Counter[str] = Counter(bucket(r.get("review_reason", "")) for r in flagged)
    print("\nreview_reason buckets (all flagged records):")
    for b, n in buckets.most_common():
        print(f"  {n:>4}  {b}")

    for b in buckets:
        matching = [r for r in flagged if bucket(r.get("review_reason", "")) == b]
        print(
            f"\n{'=' * 70}\nBucket: {b}  (showing up to {args.show_examples} of {len(matching)})\n{'=' * 70}"
        )
        for r in matching[: args.show_examples]:
            fp = r.get("manual_marks") is not None and float(r["awarded_marks"]) == float(
                r["manual_marks"]
            )
            print(
                f"\n--- {r['student_id']} {r['question_id']}  [{'FALSE POSITIVE' if fp else 'real disagreement'}] ---"
            )
            print(f"question_text: {r['question_text']!r}")
            print(
                f"extracted_answer: {r.get('extracted_answer')!r}  selected_option: {r.get('selected_option')!r}"
            )
            print(
                f"golden_answer: {r.get('golden_answer')!r}  awarded={r.get('awarded_marks')} manual={r.get('manual_marks')} confidence={r.get('confidence')}"
            )
            print(f"review_reason: {r.get('review_reason')!r}")


if __name__ == "__main__":
    main()
