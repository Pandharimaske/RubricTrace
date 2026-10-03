#!/usr/bin/env python3
"""
Compute grading-accuracy metrics from a run_pipeline() results JSON, for
reporting in the research paper.

WHY SPLIT BY QUESTION TYPE
Metrics are computed separately for "MCQ" and "short_answer" (plus a
normalized-percentage pool across both), never as one pooled raw-marks
number. MCQ correctness-checking and short-answer rubric-based partial-credit
scoring are different tasks with different score scales (max_marks=1 vs 2)
and different research claims — pooling them into one MAE/QWK would produce a
headline number that doesn't map cleanly to either claim, and would let a
strong MCQ result mask a weak short-answer result (or vice versa).

METRICS PER GROUP (only over records with a non-null manual_marks ground
truth — question_type is read straight from each result; older result files
without that field fall back to Q1-Q20=MCQ / Q21-Q35=short_answer for this
specific exam):

  n                          graded question-instances with ground truth
  mae, rmse                  magnitude of grading error (awarded vs manual)
  bias                       mean SIGNED error (awarded - manual); nonzero
                             means systematic over/under-grading, which mae
                             alone can't reveal (errors could cancel out)
  pearson_r, spearman_rho    correlation between awarded and manual marks
  qwk                        Quadratic Weighted Kappa — the standard metric
                             in the short-answer-scoring literature (e.g.
                             ASAP-SAS). Requires discrete ordinal categories,
                             so marks are rounded to the nearest half-mark
                             first (this dataset's human graders used
                             half-mark increments) and scaled to integers.
                             Skipped for the normalized-percentage pool,
                             where half-mark rounding isn't meaningful.
  exact_match_accuracy       awarded == manual after rounding to nearest 0.5
  within_0_5_accuracy /
  within_1_0_accuracy        accuracy allowing a tolerance band, in raw marks
  needs_review_precision/
  recall/f1                  treats the grader's own `needs_review` flag as a
                             binary classifier for "this grade disagrees with
                             the teacher by more than DISAGREEMENT_THRESHOLD
                             marks" — i.e. does the system's self-reported
                             uncertainty actually track real errors? This is
                             the metric that speaks to the "teacher-in-the-
                             loop" claim, not just raw accuracy.
  confidence_calibration     mean |error| by confidence quartile — a
                             well-calibrated grader shows lower error in its
                             higher-confidence bucket.

Usage:
    python scripts/compute_grading_metrics.py data/processed/mendeley_results_full.json
    python scripts/compute_grading_metrics.py <results.json> --output <report.json>
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

try:
    from scipy.stats import pearsonr, spearmanr
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "This script requires scipy (already a scikit-learn dependency in "
        "requirements.txt). If it's somehow missing: "
        "pip install scipy --break-system-packages"
    ) from exc

try:
    from sklearn.metrics import cohen_kappa_score
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "This script requires scikit-learn (already in requirements.txt). "
        "If it's somehow missing: pip install scikit-learn --break-system-packages"
    ) from exc


# How far off (in raw marks) a grade has to be from the teacher's mark before
# we count it as a "real disagreement" for the needs_review precision/recall
# check above. Half a mark on a typical 1-2 mark question is a meaningful miss,
# not rounding noise.
DISAGREEMENT_THRESHOLD = 0.5


def _round_to_half(x: float) -> float:
    return round(x * 2) / 2


def _safe_corr(fn, xs: list[float], ys: list[float]) -> float | None:
    """Pearson/Spearman are undefined (or numerically unstable) with fewer
    than 2 points or with no variance in either series — return None rather
    than letting scipy warn or divide by zero."""
    if len(xs) < 2 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    value, _p = fn(xs, ys)
    return None if math.isnan(value) else round(float(value), 4)


def _prf1(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return (
        round(precision, 4) if precision is not None else None,
        round(recall, 4) if recall is not None else None,
        round(f1, 4) if f1 is not None else None,
    )


def compute_group_metrics(
    records: list[dict[str, Any]], discrete_metrics: bool = True
) -> dict[str, Any]:
    """discrete_metrics=False skips QWK/exact-match/tolerance-band accuracy —
    used for the normalized-percentage pool, where "nearest half-mark"
    rounding doesn't correspond to anything meaningful on a 0-1 scale."""
    graded = [r for r in records if r.get("manual_marks") is not None]
    n = len(graded)
    if n == 0:
        return {"n": 0}

    manual = [float(r["manual_marks"]) for r in graded]
    awarded = [float(r["awarded_marks"]) for r in graded]
    errors = [a - m for a, m in zip(awarded, manual)]
    abs_errors = [abs(e) for e in errors]

    metrics: dict[str, Any] = {
        "n": n,
        "mae": round(sum(abs_errors) / n, 4),
        "rmse": round(math.sqrt(sum(e * e for e in errors) / n), 4),
        "bias": round(sum(errors) / n, 4),
        "pearson_r": _safe_corr(pearsonr, manual, awarded),
        "spearman_rho": _safe_corr(spearmanr, manual, awarded),
    }

    if discrete_metrics:
        manual_r = [_round_to_half(m) for m in manual]
        awarded_r = [_round_to_half(a) for a in awarded]
        metrics["exact_match_accuracy"] = round(
            sum(1 for m, a in zip(manual_r, awarded_r) if m == a) / n, 4
        )
        metrics["within_0_5_accuracy"] = round(sum(1 for e in abs_errors if e <= 0.5) / n, 4)
        metrics["within_1_0_accuracy"] = round(sum(1 for e in abs_errors if e <= 1.0) / n, 4)

        manual_int = [int(round(m * 2)) for m in manual_r]
        awarded_int = [int(round(a * 2)) for a in awarded_r]
        qwk = None
        if len(set(manual_int)) > 1 or len(set(awarded_int)) > 1:
            qwk = round(float(cohen_kappa_score(manual_int, awarded_int, weights="quadratic")), 4)
        metrics["qwk"] = qwk

    # needs_review as a binary classifier for "real disagreement" (see
    # DISAGREEMENT_THRESHOLD docstring above).
    tp = fp = fn = tn = 0
    for r, err in zip(graded, abs_errors):
        actual_disagreement = err > DISAGREEMENT_THRESHOLD
        flagged = bool(r.get("needs_review"))
        if flagged and actual_disagreement:
            tp += 1
        elif flagged and not actual_disagreement:
            fp += 1
        elif not flagged and actual_disagreement:
            fn += 1
        else:
            tn += 1
    precision, recall, f1 = _prf1(tp, fp, fn)
    # Teacher-in-the-loop payoff: if the teacher re-marks every flagged answer (their
    # mark replaces the system's), what is the MAE over the whole set?
    metrics["share_flagged"] = round((tp + fp) / n, 4)
    metrics["mae_after_teacher_review"] = round(
        sum(0.0 if r.get("needs_review") else e for r, e in zip(graded, abs_errors)) / n, 4
    )
    metrics["needs_review_precision"] = precision
    metrics["needs_review_recall"] = recall
    metrics["needs_review_f1"] = f1
    metrics["needs_review_confusion"] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn}

    # Confidence calibration: mean |error| by confidence quartile. A
    # well-calibrated grader shows lower error in its higher-confidence
    # bucket; if error doesn't trend down with confidence, the confidence
    # score isn't currently a useful triage signal.
    paired = sorted(
        ((float(r.get("confidence") or 0.0), e) for r, e in zip(graded, abs_errors)),
        key=lambda pair: pair[0],
    )
    calibration = []
    if paired:
        bucket_size = max(1, len(paired) // 4)
        for i in range(0, len(paired), bucket_size):
            bucket = paired[i : i + bucket_size]
            if not bucket:
                continue
            confs = [c for c, _ in bucket]
            errs = [e for _, e in bucket]
            calibration.append(
                {
                    "confidence_range": [round(min(confs), 3), round(max(confs), 3)],
                    "n": len(bucket),
                    "mean_abs_error": round(sum(errs) / len(errs), 4),
                }
            )
    metrics["confidence_calibration"] = calibration

    return metrics


def _infer_question_type(question_id: str) -> str:
    """Fallback for result files predating the question_type field: this
    specific exam has Q1-Q20 as MCQ, Q21-Q35 as short answer. Not a general
    rule — new manifests should carry question_type explicitly."""
    try:
        n = int(question_id.lstrip("Q"))
    except ValueError:
        return "unknown"
    return "MCQ" if 1 <= n <= 20 else "short_answer"


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        qtype = r.get("question_type") or _infer_question_type(r.get("question_id", ""))
        groups.setdefault(qtype, []).append(r)

    report: dict[str, Any] = {}
    for name, records in groups.items():
        if records:
            report[name] = compute_group_metrics(records, discrete_metrics=True)

    # Overall, normalized to % of max_marks so MCQ (max=1) and short-answer
    # (max=2) pool onto a comparable 0-1 scale rather than being combined in
    # raw marks, where a short-answer question would silently outweigh an MCQ.
    normalized_pool = []
    for r in results:
        if r.get("manual_marks") is None or not r.get("max_marks"):
            continue
        max_marks = float(r["max_marks"])
        normalized_pool.append(
            {
                **r,
                "manual_marks": float(r["manual_marks"]) / max_marks,
                "awarded_marks": float(r["awarded_marks"]) / max_marks,
            }
        )
    if normalized_pool:
        report["overall_normalized_pct"] = compute_group_metrics(
            normalized_pool, discrete_metrics=False
        )

    # Student-level view (short-answer only): per-question stats hide how errors add up.
    # Total marks per student over the graded short-answer questions, awarded vs manual.
    sa = [r for r in groups.get("short_answer", []) if r.get("manual_marks") is not None]
    if sa:
        totals: dict[str, list[float]] = {}
        for r in sa:
            t = totals.setdefault(r["student_id"], [0.0, 0.0])
            t[0] += float(r["manual_marks"])
            t[1] += float(r["awarded_marks"])
        man = [v[0] for v in totals.values()]
        aw = [v[1] for v in totals.values()]
        diffs = [a - m for a, m in zip(aw, man)]
        report["student_level_short_answer"] = {
            "n_students": len(totals),
            "mae_total_marks": round(sum(abs(d) for d in diffs) / len(diffs), 3),
            "bias_total_marks": round(sum(diffs) / len(diffs), 3),
            "mean_manual_total": round(sum(man) / len(man), 3),
            "pearson_r": _safe_corr(pearsonr, man, aw),
            "spearman_rho": _safe_corr(spearmanr, man, aw),
        }

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("results_path", type=Path, help="Path to a run_pipeline() output JSON")
    parser.add_argument(
        "--output", type=Path, default=None, help="Optional path to write the report as JSON"
    )
    parser.add_argument(
        "--split-file",
        type=Path,
        default=None,
        help="eval_split.json ({'dev': [...], 'test': [...]}). Use with --split.",
    )
    parser.add_argument(
        "--split",
        choices=["dev", "test"],
        default=None,
        help="Only score this student split (report headline numbers on 'test').",
    )
    args = parser.parse_args()

    payload = json.loads(args.results_path.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    if args.split:
        if not args.split_file:
            raise SystemExit("--split needs --split-file")
        wanted = set(json.loads(args.split_file.read_text(encoding="utf-8"))[args.split])
        results = [r for r in results if r.get("student_id") in wanted]
        print(f"[split={args.split}] scoring {len(results)} records from {len(wanted)} students\n")
    if not results:
        raise SystemExit(f"No results found in {args.results_path}")

    report = build_report(results)

    print(json.dumps(report, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote report to {args.output}")


if __name__ == "__main__":
    main()
