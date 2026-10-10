#!/usr/bin/env python3
"""
Grade every MCQ record with an NVIDIA-hosted model (openai/gpt-oss-20b by default) via
the lean llm_grade_mcq() path — correct/incorrect only, no reasoning chain.

Designed to run standalone and CONCURRENTLY with grade_short_answer_cloud.py
(see run_grading_parallel.sh): the two write to separate output files and share no state.
This script stays sequential (one request at a time) so it leaves most of the NVIDIA
rate-limit budget to the short-answer track, which runs several requests in flight.

Input
-----
--manifest   MCQ manifest JSON (student_id, question_id, question_text,
             golden_answer, max_marks, manual_marks per record). Defaults to
             data/processed/mendeley_manifest_mcq.json.
--extracted  A results JSON (same shape run_pipeline()/pipeline_results.json
             produce) that carries the OCR/VLM-extracted "extracted_answer"
             text per (student_id, question_id). Defaults to
             data/processed/extracted_answers_index.json — the consolidated
             index built by scripts/build_extracted_answers_index.py from the
             per-student VLM extraction files in
             data/raw/extracted_answers/. Run that script first if this file
             doesn't exist yet or the source extractions have changed.

Output
------
--output     Checkpointed JSON, same {"summary":..., "results":[...]} shape
             compute_grading_metrics.py expects. Defaults to
             data/processed/results_mcq.json. Re-running resumes: any
             (student_id, question_id) already present is skipped.

Edge cases handled explicitly (see inline comments):
  - blank/whitespace extracted answer -> scored 0, NOT flagged for review, no
    LLM call (a skipped question is the expected case, not an extraction failure;
    same rule as grade_short_answer_cloud.py)
  - no extraction record at all for a (student, question) pair -> same as blank
  - LLM/backend failure after generate_structured's internal retries -> scored
    0, needs_review, review_reason carries the error, run continues
  - awarded_marks is derived from is_correct x max_marks (never asked of the
    model directly) so a hallucinated numeric score is not possible
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _legacy import DEFAULT_REVIEW_THRESHOLD, clamp_review_threshold, llm_grade_mcq, review_verdict
from backend.app.core.config import PROCESSED_DIR, settings
from backend.app.llm.errors import ModelUnavailable
from backend.app.llm.nvidia import NvidiaClient

DEFAULT_MODEL = settings.nvidia_llm_model


def _load_manifest(path: Path) -> tuple[list[dict[str, Any]], float | None]:
    """(records, the exam's review_confidence_threshold if the manifest carries one)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        threshold = payload.get("review_confidence_threshold")
        return payload["records"], (
            clamp_review_threshold(threshold) if threshold is not None else None
        )
    return payload, None


def _load_extracted_answers(path: Path) -> dict[tuple[str, str], str]:
    """Map (student_id, question_id) -> extracted_answer text from a prior
    extraction/results pass. Missing file or missing key both resolve to ""
    at lookup time (treated as a blank answer)."""
    if not path.exists():
        print(
            f"WARNING: extracted-answers file not found: {path} "
            "(every answer will be treated as blank)",
            flush=True,
        )
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("results", payload) if isinstance(payload, dict) else payload
    out: dict[tuple[str, str], str] = {}
    for r in records:
        key = (str(r.get("student_id", "")), str(r.get("question_id", "")))
        out[key] = str(r.get("extracted_answer") or "")
    return out


def _blank_result(
    record: dict[str, Any],
    extracted_answer: str,
    reason: str,
    needs_review: bool = True,
    confidence: float = 0.0,
) -> dict[str, Any]:
    return {
        "student_id": record["student_id"],
        "question_id": record["question_id"],
        "question_type": "MCQ",
        "question_text": record.get("question_text", ""),
        "golden_answer": record.get("golden_answer", ""),
        "extracted_answer": extracted_answer,
        "reasoning": "",
        "max_marks": float(record["max_marks"]),
        "manual_marks": record.get("manual_marks"),
        "awarded_marks": 0.0,
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": reason,
        "absolute_error": (
            round(abs(0.0 - float(record["manual_marks"])), 2)
            if record.get("manual_marks") is not None
            else None
        ),
        "llm_model": None,
        "status": "needs_review" if needs_review else "scored",
    }


def grade_record(
    record: dict[str, Any],
    extracted_answer: str,
    client: NvidiaClient,
    model: str,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    if not extracted_answer.strip():
        # Nothing to grade: score 0 deterministically, no LLM call, and keep it out of the
        # review queue. confidence=1.0 is certainty in the 0-mark decision itself.
        return _blank_result(
            record,
            extracted_answer,
            "No answer extracted -- question appears to have been left blank.",
            needs_review=False,
            confidence=1.0,
        )

    try:
        llm_result, llm_model = llm_grade_mcq(
            question_id=record["question_id"],
            student_answer=extracted_answer,
            golden_answer=record.get("golden_answer", ""),
            question_text=record.get("question_text", ""),
            client=client,
            model=model,
        )
    except ModelUnavailable as exc:
        result = _blank_result(record, extracted_answer, f"Grading failed: {exc}")
        result["status"] = "needs_review"
        return result

    max_marks = float(record["max_marks"])
    awarded = max_marks if llm_result["is_correct"] else 0.0
    manual_marks = record.get("manual_marks")
    # needs_review is derived from confidence alone, against the exam's threshold --
    # the grader never returns it (see grader.py's module docstring).
    needs_review, review_reason = review_verdict(llm_result["confidence"], review_threshold)

    return {
        "student_id": record["student_id"],
        "question_id": record["question_id"],
        "question_type": "MCQ",
        "question_text": record.get("question_text", ""),
        "golden_answer": record.get("golden_answer", ""),
        "extracted_answer": extracted_answer,
        "reasoning": llm_result.get("reasoning", ""),
        "max_marks": max_marks,
        "manual_marks": manual_marks,
        "awarded_marks": awarded,
        "confidence": llm_result["confidence"],
        "needs_review": needs_review,
        "review_reason": review_reason,
        "absolute_error": (
            round(abs(awarded - float(manual_marks)), 2) if manual_marks is not None else None
        ),
        "llm_model": llm_model,
        "status": "needs_review" if needs_review else "scored",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest", type=Path, default=PROCESSED_DIR / "mendeley_manifest_mcq.json"
    )
    parser.add_argument(
        "--extracted", type=Path, default=PROCESSED_DIR / "extracted_answers_index.json"
    )
    parser.add_argument("--output", type=Path, default=PROCESSED_DIR / "results_mcq.json")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"NVIDIA model ID (default {DEFAULT_MODEL}; set RUBRICTRACE_NVIDIA_LLM_MODEL "
        "to change the default)",
    )
    parser.add_argument(
        "--review-threshold",
        type=float,
        default=None,
        help="Confidence below this is flagged needs_review. Default: the manifest's "
        "review_confidence_threshold (from exam_rubric.json), else "
        f"{DEFAULT_REVIEW_THRESHOLD}.",
    )
    parser.add_argument(
        "--student", action="append", help="Only grade this student_id. Repeatable."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only grade the first N pending records (smoke test).",
    )
    args = parser.parse_args()

    records, manifest_threshold = _load_manifest(args.manifest)
    review_threshold = clamp_review_threshold(
        args.review_threshold
        if args.review_threshold is not None
        else (manifest_threshold if manifest_threshold is not None else DEFAULT_REVIEW_THRESHOLD)
    )
    print(f"[mcq] review threshold: {review_threshold}", flush=True)
    if args.student:
        wanted = set(args.student)
        records = [r for r in records if r["student_id"] in wanted]
        if not records:
            raise SystemExit(f"No records matched --student {args.student} in {args.manifest}")

    extracted = _load_extracted_answers(args.extracted)
    client = NvidiaClient()

    results: list[dict[str, Any]] = []
    done_keys: set[tuple[str, str]] = set()
    if args.output.exists():
        try:
            previous = json.loads(args.output.read_text(encoding="utf-8"))
            results = previous.get("results", [])
            done_keys = {(r["student_id"], r["question_id"]) for r in results}
        except (json.JSONDecodeError, KeyError):
            results = []
        if done_keys:
            print(
                f"Resuming {args.output.name}: {len(done_keys)} question-results already saved, skipping those.",
                flush=True,
            )

    def _flush() -> None:
        evaluated = [r for r in results if r.get("absolute_error") is not None]
        correct = sum(1 for r in evaluated if r["absolute_error"] == 0)
        summary = {
            "review_confidence_threshold": review_threshold,
            "records": len(results),
            "scored": sum(r["status"] == "scored" for r in results),
            "needs_review": sum(r["status"] == "needs_review" for r in results),
            "manual_mark_records": len(evaluated),
            "accuracy": round(correct / len(evaluated), 4) if evaluated else None,
            "mae": round(sum(r["absolute_error"] for r in evaluated) / len(evaluated), 4)
            if evaluated
            else None,
        }
        payload = {"summary": summary, "results": results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    pending = [r for r in records if (r["student_id"], r["question_id"]) not in done_keys]
    if args.limit is not None:
        pending = pending[: args.limit]

    print(
        f"[mcq] {len(pending)} pending record(s) to grade with {args.model} (NVIDIA)…",
        flush=True,
    )

    for i, record in enumerate(pending, start=1):
        key = (record["student_id"], record["question_id"])
        answer = extracted.get(key, "")
        result = grade_record(record, answer, client, args.model, review_threshold)
        results.append(result)

        mark_note = (
            f" (manual={result['manual_marks']}, err={result['absolute_error']})"
            if result.get("absolute_error") is not None
            else ""
        )
        print(
            f"  [{i}/{len(pending)}] {result['student_id']} {result['question_id']}: "
            f"awarded={result['awarded_marks']}/{result['max_marks']} conf={result['confidence']}"
            f"{mark_note}",
            flush=True,
        )

        if i % 10 == 0 or i == len(pending):
            _flush()

    _flush()
    print(f"[mcq] Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
