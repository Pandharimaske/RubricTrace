#!/usr/bin/env python3
"""
Break down short-answer grading error by question_id, to see whether the
-0.61 aggregate bias and the low needs_review recall are concentrated on a
few questions (fixable with targeted rubric/prompt edits, as with Q25) or
spread evenly across all 15 (a harder, more general calibration problem).

Usage:
    python scripts/analyze_short_answer_by_question.py
    python scripts/analyze_short_answer_by_question.py --results data/processed/results_short_answer.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

DISAGREEMENT_THRESHOLD = 0.5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data/processed/results_short_answer.json",
    )
    args = parser.parse_args()

    payload = json.loads(args.results.read_text(encoding="utf-8"))
    results = payload.get("results", payload)

    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        if r.get("manual_marks") is not None:
            by_q[r["question_id"]].append(r)

    rows = []
    for qid, recs in by_q.items():
        n = len(recs)
        errors = [float(r["awarded_marks"]) - float(r["manual_marks"]) for r in recs]
        abs_errors = [abs(e) for e in errors]
        mae = sum(abs_errors) / n
        bias = sum(errors) / n
        disagreements = [r for r, e in zip(recs, abs_errors) if e > DISAGREEMENT_THRESHOLD]
        flagged_among_disagreements = sum(1 for r in disagreements if r.get("needs_review"))
        recall = flagged_among_disagreements / len(disagreements) if disagreements else None
        rows.append(
            {
                "question_id": qid,
                "n": n,
                "mae": round(mae, 3),
                "bias": round(bias, 3),
                "n_disagreements": len(disagreements),
                "review_recall": round(recall, 3) if recall is not None else None,
            }
        )

    rows.sort(key=lambda r: r["mae"], reverse=True)
    print(f"{'Q':<6}{'n':>5}{'MAE':>8}{'bias':>8}{'n_dis':>8}{'recall':>9}")
    for r in rows:
        recall_str = f"{r['review_recall']}" if r["review_recall"] is not None else "-"
        print(
            f"{r['question_id']:<6}{r['n']:>5}{r['mae']:>8}{r['bias']:>8}{r['n_disagreements']:>8}{recall_str:>9}"
        )


if __name__ == "__main__":
    main()
