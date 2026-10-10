#!/usr/bin/env python3
"""
Grade every short-answer record with NVIDIA NIM via the
full-reasoning llm_grade() path — partial credit, rubric-criterion breakdown,
reasoning/evidence trace, confidence, needs_review.

Designed to run standalone and CONCURRENTLY with grade_mcq.py (see
run_grading_parallel.sh): the two write to separate output files and share no state.
This one can serve several requests in flight (--concurrency); grade_mcq.py stays
sequential.

Marks are constrained to the assignment's discrete half-mark scale
{0, 0.5, 1, 1.5, 2}: the model is instructed to use only those values, and the
awarded mark is snapped to the nearest half-mark and clipped to
[0, max_marks] as a code-level safety net regardless of what the model returns.

Input
-----
--manifest    Short-answer manifest JSON (student_id, question_id,
              question_text, golden_answer, max_marks, manual_marks, criteria
              per record). Defaults to
              data/processed/mendeley_manifest_short_answer.json.
--extracted   A results JSON carrying the OCR/VLM-extracted "extracted_answer"
              text per (student_id, question_id). Defaults to
              data/processed/extracted_answers_index.json — the consolidated
              index built by scripts/build_extracted_answers_index.py from the
              per-student VLM extraction files in
              data/raw/extracted_answers/. Run that script first if this file
              doesn't exist yet or the source extractions have changed.

Output
------
--output      Checkpointed JSON, same {"summary":..., "results":[...]} shape
              compute_grading_metrics.py expects. Defaults to
              data/processed/results_short_answer.json. Re-running resumes:
              any (student_id, question_id) already present is skipped.

Provider
--------
--provider    NVIDIA is the only supported cloud provider. This script requires
              NVIDIA_API_KEY in .env. The model defaults to
              RUBRICTRACE_NVIDIA_LLM_MODEL (openai/gpt-oss-20b); use --model to
              override it for this run.

Edge cases handled explicitly (see inline comments):
  - blank/whitespace extracted answer -> scored 0, NOT flagged for review, no
    LLM call. A skipped question is the expected common case, not evidence of
    an extraction failure -- flagging every blank made up 172 of 274 (63%) of
    the short-answer review queue for no real signal.
  - no extraction record at all for a (student, question) pair -> same as blank
  - LLM/backend failure after generate_structured's internal retries (which
    already covers transient rate-limit/network errors with backoff) ->
    scored 0, needs_review, review_reason carries the error, run continues
  - awarded_marks is snapped to the nearest half-mark and clipped to
    [0, max_marks] regardless of what the model returns
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _legacy import DEFAULT_REVIEW_THRESHOLD, clamp_review_threshold, llm_grade, review_verdict
from backend.app.core.config import PROCESSED_DIR, settings
from backend.app.llm.errors import ModelUnavailable

DEFAULT_CONCURRENCY = 4
DEFAULT_PROVIDER_CHAIN = "nvidia"

# Substrings that identify an exhausted NVIDIA account rather than a transient
# failure. The script records those failures for review and continues the run.
_EXHAUSTION_MARKERS = (
    "insufficient_quota",
    "quota",
    "credit",
    "billing",
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
)


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


def _make_single_client(provider: str, model_override: str | None) -> tuple[Any, str]:
    provider = provider.strip().lower()
    if provider == "nvidia":
        from backend.app.llm.nvidia import NvidiaClient

        model = model_override or settings.nvidia_llm_model
        return NvidiaClient(), model
    raise SystemExit(f"Unknown provider {provider!r}; only 'nvidia' is supported.")


class _FallbackClient:
    """
    Wraps an ordered chain of cloud clients serving the
    SAME model, and permanently switches the whole run from the active one to
    the next the first time a call fails with a credit/quota/rate-limit
    marker (see _EXHAUSTION_MARKERS) — see the module docstring's "Provider +
    automatic credit/quota failover" section for the full rationale.

    Thread-safe: grade_short_answer_cloud.py shares one instance across all
    ThreadPoolExecutor workers. A lock guards the active-provider index so
    concurrent callers agree on which provider is "current," and so only one
    of them prints the switch-over notice.
    """

    def __init__(self, clients: list[tuple[str, Any]]) -> None:
        if not clients:
            raise ValueError("_FallbackClient needs at least one (provider_name, client) pair")
        self._clients = clients  # ordered [(name, client), ...]
        self._lock = threading.Lock()
        self._active_index = 0

    @property
    def active_provider(self) -> str:
        with self._lock:
            return self._clients[self._active_index][0]

    @staticmethod
    def _looks_like_exhaustion(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in _EXHAUSTION_MARKERS)

    def _advance_past(self, exhausted_index: int) -> bool:
        """Move the active index past exhausted_index, if a next provider exists.
        Returns True if the run now has a (possibly different) active provider
        to retry with, False if the whole chain is exhausted."""
        with self._lock:
            if self._active_index > exhausted_index:
                return True  # another thread already advanced past this one
            if exhausted_index + 1 >= len(self._clients):
                return False
            self._active_index = exhausted_index + 1
            exhausted_name = self._clients[exhausted_index][0]
            new_name = self._clients[self._active_index][0]
            print(
                f"    [fallback] {exhausted_name} looks exhausted (credits/quota/rate limit) "
                f"— switching the rest of this run to {new_name}.",
                flush=True,
            )
            return True

    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list[Path] | None = None,
        json_output: bool = False,
    ) -> str:
        while True:
            with self._lock:
                idx = self._active_index
                _, client = self._clients[idx]
            try:
                return client.generate(model, prompt, image_paths, json_output)
            except ModelUnavailable as exc:
                if self._looks_like_exhaustion(exc) and self._advance_past(idx):
                    continue  # retry immediately on the newly-active provider
                raise


def _build_client(provider_arg: str, model_override: str | None) -> tuple[Any, str]:
    """Builds either a single client (one provider given) or a _FallbackClient
    (more than one, comma-separated) — same return shape either way, so
    callers don't need to care which. All providers in the chain must resolve
    to the same model (see docstring); the first provider's model wins if
    --model wasn't passed and the providers' env-var defaults happen to
    differ, though .env should set them equal for this to be meaningful."""
    provider_names = [p.strip().lower() for p in provider_arg.split(",") if p.strip()]
    if not provider_names:
        raise SystemExit("--provider must name at least one provider.")

    built = [(name, *_make_single_client(name, model_override)) for name in provider_names]
    # built: list of (name, client, model)
    model = built[0][2]
    mismatched = {name: m for name, _client, m in built if m != model}
    if mismatched:
        print(
            f"WARNING: providers in the fallback chain are pointed at different models "
            f"({built[0][0]}={model!r}, {mismatched}) — a mid-run failover will change "
            "which model grades the rest of the run.",
            flush=True,
        )

    if len(built) == 1:
        return built[0][1], model
    return _FallbackClient([(name, client) for name, client, _m in built]), model


def _snap_to_half_mark(value: float, max_marks: float, step: float = 0.5) -> float:
    """Snap to the nearest multiple of `step`, rounding halves UP (Python's round()
    rounds halves to even, which would send 0.5 to 0 but 1.5 to 2)."""
    snapped = math.floor(value / step + 0.5) * step
    return max(0.0, min(snapped, max_marks))


def _half_mark_rubric_note(max_marks: float, step: float = 0.5) -> str:
    n = int(round(max_marks / step))
    steps = [round(i * step, 1) for i in range(n + 1)]
    scale = "whole-mark" if step >= 1.0 else "half-mark"
    return (
        f"This question is marked on a fixed {scale} scale — award ONLY one of "
        f"these exact values: {steps}. Do not award any other number. The human "
        "markers for this exam use this same scale: if the criteria add up to a value "
        "between two allowed marks, or you are torn between two adjacent marks, "
        "award the HIGHER one."
    )


def _blank_result(
    record: dict[str, Any],
    extracted_answer: str,
    reason: str,
    provider_used: str | None = None,
    needs_review: bool = True,
    confidence: float = 0.0,
) -> dict[str, Any]:
    max_marks = float(record["max_marks"])
    manual_marks = record.get("manual_marks")
    return {
        "student_id": record["student_id"],
        "question_id": record["question_id"],
        "question_type": "short_answer",
        "question_text": record.get("question_text", ""),
        "golden_answer": record.get("golden_answer", ""),
        "extracted_answer": extracted_answer,
        "rubric": record.get("criteria", []),
        "max_marks": max_marks,
        "manual_marks": manual_marks,
        "awarded_marks": 0.0,
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": reason,
        "reasoning": "",
        "evidence": [],
        "concepts_found": [],
        "concepts_missing": [],
        "criteria_satisfied": [],
        "criteria_partial": [],
        "criteria_failed": [],
        "absolute_error": round(abs(0.0 - float(manual_marks)), 2)
        if manual_marks is not None
        else None,
        "llm_model": None,
        "provider_used": provider_used,
        "status": "needs_review" if needs_review else "scored",
    }


def grade_record(
    record: dict[str, Any],
    extracted_answer: str,
    client: Any,
    model: str,
    default_provider: str,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    mark_step: float = 0.5,
    flag_zero: bool = False,
) -> dict[str, Any]:
    if not extracted_answer.strip():
        # A blank extraction is NOT automatically a problem worth a teacher's
        # time: not every student answers every question, and this is the
        # expected, common case for a skipped question -- not evidence of an
        # OCR/VLM failure. Score it 0 deterministically (no LLM call needed --
        # there's nothing to grade) and leave it OUT of the review queue.
        # confidence=1.0 here reflects certainty in the 0-mark decision itself
        # (there is genuinely nothing to grade), not a model judgment -- this
        # deliberately avoids the same low-confidence-but-not-flagged shape we
        # had to debug for MCQ; here it's high-confidence and not flagged,
        # which is internally consistent.
        return _blank_result(
            record,
            extracted_answer,
            "No answer extracted -- question appears to have been left blank.",
            needs_review=False,
            confidence=1.0,
        )

    max_marks = float(record["max_marks"])
    manual_marks = record.get("manual_marks")
    guidance = _half_mark_rubric_note(max_marks, mark_step)
    if record.get("guidance"):
        guidance += "\n\nQuestion-specific grading notes:\n" + str(record["guidance"])
    provider_used = getattr(client, "active_provider", default_provider)

    try:
        llm_result, llm_model = llm_grade(
            question_id=record["question_id"],
            student_answer=extracted_answer,
            golden_answer=record.get("golden_answer", ""),
            rubric=record.get("criteria", []),
            max_marks=max_marks,
            guidance=guidance,
            question_text=record.get("question_text", ""),
            question_type="short_answer",
            client=client,
            model=model,
        )
    except ModelUnavailable as exc:
        # Whole chain exhausted (or a non-exhaustion failure survived
        # generate_structured's retries) — record whichever provider was
        # active when it finally gave up.
        provider_used = getattr(client, "active_provider", default_provider)
        result = _blank_result(record, extracted_answer, f"Grading failed: {exc}", provider_used)
        result["status"] = "needs_review"
        return result

    # The provider may have failed over mid-call; re-read it after the fact so
    # the record reflects who actually produced this answer, not who was
    # active when grading started.
    provider_used = getattr(client, "active_provider", default_provider)
    awarded = _snap_to_half_mark(float(llm_result["awarded_marks"]), max_marks, mark_step)
    # needs_review is derived from confidence alone, against the exam's threshold --
    # the grader never returns it (see grader.py's module docstring).
    needs_review, review_reason = review_verdict(llm_result["confidence"], review_threshold)
    # Confidence alone is a weak triage signal here (most answers sit at exactly 0.95),
    # so also flag every non-blank answer the grader scored 0: on the first eval run
    # those were the most reliably under-graded answers.
    if flag_zero and awarded == 0.0 and extracted_answer.strip():
        needs_review = True
        review_reason = (
            f"{review_reason}; " if review_reason else ""
        ) + "Awarded 0 on a non-blank answer (likely under-grade)."

    return {
        "student_id": record["student_id"],
        "question_id": record["question_id"],
        "question_type": "short_answer",
        "question_text": record.get("question_text", ""),
        "golden_answer": record.get("golden_answer", ""),
        "extracted_answer": extracted_answer,
        "rubric": record.get("criteria", []),
        "max_marks": max_marks,
        "manual_marks": manual_marks,
        "awarded_marks": awarded,
        "confidence": llm_result["confidence"],
        "needs_review": needs_review,
        "review_reason": review_reason,
        "reasoning": llm_result.get("reasoning", ""),
        "evidence": llm_result.get("evidence", []),
        "concepts_found": llm_result.get("concepts_found", []),
        "concepts_missing": llm_result.get("concepts_missing", []),
        "criteria_satisfied": llm_result.get("criteria_satisfied", []),
        "criteria_partial": llm_result.get("criteria_partial", []),
        "criteria_failed": llm_result.get("criteria_failed", []),
        "absolute_error": (
            round(abs(awarded - float(manual_marks)), 2) if manual_marks is not None else None
        ),
        "llm_model": llm_model,
        "provider_used": provider_used,
        "status": "needs_review" if needs_review else "scored",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest", type=Path, default=PROCESSED_DIR / "mendeley_manifest_short_answer.json"
    )
    parser.add_argument(
        "--extracted", type=Path, default=PROCESSED_DIR / "extracted_answers_index.json"
    )
    parser.add_argument("--output", type=Path, default=PROCESSED_DIR / "results_short_answer.json")
    parser.add_argument(
        "--provider",
        default=os.getenv("RUBRICTRACE_SHORT_ANSWER_PROVIDER", DEFAULT_PROVIDER_CHAIN),
        help=f"Comma-separated provider chain, tried in order (default {DEFAULT_PROVIDER_CHAIN!r}). "
        "Pass a single name (e.g. 'nvidia') to disable failover.",
    )
    parser.add_argument(
        "--model", default=None, help="Override the model for every provider in the chain."
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
        "--concurrency",
        type=int,
        default=int(os.getenv("RUBRICTRACE_GRADING_CONCURRENCY", str(DEFAULT_CONCURRENCY))),
        help=f"Parallel in-flight cloud requests (default {DEFAULT_CONCURRENCY}). "
        "Lower this if you hit rate limits.",
    )
    parser.add_argument(
        "--mark-step",
        type=float,
        default=1.0,
        help="Mark granularity the grader must use and results are snapped to (halves round UP). "
        "1.0 = whole marks (default; the human markers used whole marks 97%% of the time), "
        "0.5 = half marks (the original behaviour).",
    )
    parser.add_argument(
        "--no-flag-zero",
        action="store_true",
        help="Disable the extra rule that flags non-blank answers scored 0 for review.",
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
    print(f"[short_answer] review threshold: {review_threshold}", flush=True)
    if args.student:
        wanted = set(args.student)
        records = [r for r in records if r["student_id"] in wanted]
        if not records:
            raise SystemExit(f"No records matched --student {args.student} in {args.manifest}")

    extracted = _load_extracted_answers(args.extracted)
    client, model = _build_client(args.provider, args.model)
    default_provider = args.provider.split(",")[0].strip().lower()

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

    flush_lock = threading.Lock()

    def _flush() -> None:
        evaluated = [r for r in results if r.get("absolute_error") is not None]
        summary = {
            "review_confidence_threshold": review_threshold,
            "records": len(results),
            "scored": sum(r["status"] == "scored" for r in results),
            "needs_review": sum(r["status"] == "needs_review" for r in results),
            "manual_mark_records": len(evaluated),
            "mae": round(sum(r["absolute_error"] for r in evaluated) / len(evaluated), 4)
            if evaluated
            else None,
        }
        payload = {"summary": summary, "results": results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with flush_lock:
            args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    pending = [r for r in records if (r["student_id"], r["question_id"]) not in done_keys]
    if args.limit is not None:
        pending = pending[: args.limit]

    print(
        f"[short_answer] {len(pending)} pending record(s) to grade with {args.provider}/{model} "
        f"(concurrency={args.concurrency})…",
        flush=True,
    )

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {
            pool.submit(
                grade_record,
                record,
                extracted.get((record["student_id"], record["question_id"]), ""),
                client,
                model,
                default_provider,
                review_threshold,
                args.mark_step,
                not args.no_flag_zero,
            ): record
            for record in pending
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _blank_result(
                    record,
                    extracted.get((record["student_id"], record["question_id"]), ""),
                    f"Unexpected error: {exc}",
                    getattr(client, "active_provider", default_provider),
                )

            results.append(result)
            completed += 1

            mark_note = (
                f" (manual={result['manual_marks']}, err={result['absolute_error']})"
                if result.get("absolute_error") is not None
                else ""
            )
            print(
                f"  [{completed}/{len(pending)}] {result['student_id']} {result['question_id']}: "
                f"awarded={result['awarded_marks']}/{result['max_marks']}{mark_note}",
                flush=True,
            )

            if completed % 10 == 0 or completed == len(pending):
                _flush()

    _flush()
    print(f"[short_answer] Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
