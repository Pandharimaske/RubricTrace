"""Single-pass VLM extraction: one structured call per page image -> merge into one
question_id -> answer mapping.

Each page goes to the VLM once. The model returns a transcript (`raw_text`) plus the
page's answers attached to question numbers, in one JSON object, with the exam's
question IDs and types in the prompt. There is no separate text-LLM structuring
stage: the old two-stage flow lost layout cues (circled/ticked MCQ options,
strikeouts, continuations) between stages, and a digit misread in stage one became
stage two's ground truth.

This is the only module in the extraction pipeline that talks to a model client.
Prompt text lives in prompts/extraction.py; pure merge/validation logic lives in
extraction/merge.py and extraction/review_flags.py; per-run counters live in
extraction/metrics.py; the per-page result cache lives in extraction/cache.py.

Safety nets, and where each one lives:
  - a question ID outside the exam's expected list is dropped, not silently
    merged in (merge.py)
  - a blank/near-blank page never reaches the VLM at all, so it can't be a
    source of hallucinated content (ocr/blank_page.py)
  - a page the model reports as empty (no raw_text, no answers) is skipped
  - an answer the model marks "illegible" is stored with its best partial reading,
    capped at low confidence, and flagged for teacher review (this module)
  - a question number that appears in the model's own raw_text but has no entry
    in its answers list triggers one retry naming the gap (this module)
  - a duplicate question ID across pages is only auto-merged if the model marked
    it a continuation, or every other expected ID is already filled; otherwise
    it's flagged as a conflict for manual review (merge.py)
  - an answer value repeated identically across an unusual number of distinct
    question IDs on one page (a sign of one fabricated value for an illegible
    section) is caught before merge and stored separately (review_flags.py)
  - substantial content that can't be attached to any question number is surfaced
    as an unassigned entry with a `suggested_question_id` best guess
    (review_flags.py)
  - a valid question ID that never received any entry across the whole script is
    flagged once every page has been processed (review_flags.py)

The first two bullets are "structural" (some information genuinely isn't
recoverable from a page image); the rest are "prompt-quality" signals expected to
fire less as the prompt improves. See extraction/metrics.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from backend.app.services.models.extraction import cache
from backend.app.services.models.extraction.merge import merge_questions, normalize_qid
from backend.app.services.models.extraction.metrics import ExtractionMetrics
from backend.app.services.models.extraction.review_flags import (
    extract_suspicious_duplicates,
    find_never_mentioned_ids,
    numbers_mentioned_in_transcript,
    record_never_mentioned,
    record_suspicious_duplicates,
    record_unassigned,
)
from backend.app.services.models.ollama import ModelUnavailable
from backend.app.services.models.prompts.extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_extraction_user_prompt,
)
from backend.app.services.models.provider import ModelClient, get_vlm_client_and_model
from backend.app.services.models.structured import (
    PageExtraction,
    VlmExtractionTranscription,
    generate_structured,
)
from backend.app.services.ocr.blank_page import is_blank_page

# An answer the model marked illegible never counts as confident, whatever
# number it reported.
ILLEGIBLE_CONFIDENCE_CAP = 0.3


def _recover_missing(
    client: ModelClient,
    model: str,
    prompt: str,
    page_path: Path,
    parsed: PageExtraction,
    allowed_ids: set[str] | None,
    metrics: ExtractionMetrics,
) -> PageExtraction:
    """PROMPT-QUALITY check: if the model's own raw_text mentions a question
    number that its answers list omits, retry once naming exactly what to
    double-check. Only the answers for the missing numbers are taken from the
    retry; everything else keeps the first result."""
    found_ids = {normalize_qid(a.question_id) for a in parsed.answers}
    gap = numbers_mentioned_in_transcript(parsed.raw_text, allowed_ids) - found_ids
    if not gap:
        return parsed

    metrics.completeness_retries_triggered += 1
    print(
        f"    [extract] raw_text mentions {sorted(gap)} but the answers list "
        "doesn't include them; retrying with a completeness nudge",
        flush=True,
    )
    nudge_prompt = prompt + (
        "\n\nBefore finalizing, look at the page image again: your raw_text mentions "
        f"question number(s) {sorted(gap)} but your answers list has no entry for "
        "them. If the page has real answer content (or a visibly blank answer space) "
        "for any of them, add an entry. If on closer look one genuinely isn't on "
        "this page, leave it out. Return the complete JSON again."
    )
    try:
        retry = generate_structured(client, model, nudge_prompt, PageExtraction, [page_path])
    except ModelUnavailable as exc:
        print(
            f"    [extract] completeness retry failed ({exc}), keeping original result", flush=True
        )
        return parsed

    assert isinstance(retry, PageExtraction)
    recovered = [a for a in retry.answers if normalize_qid(a.question_id) in gap]
    if recovered:
        metrics.completeness_retries_recovered += 1
        print(
            f"    [extract] recovered {sorted({normalize_qid(a.question_id) for a in recovered})} "
            "on completeness retry",
            flush=True,
        )
        parsed.answers.extend(recovered)
    return parsed


def _read_page(
    client: ModelClient,
    model: str,
    page_path: Path,
    question_ids: list[str] | None,
    question_types: dict[str, str] | None,
    previous_question_id: str | None,
    allowed_ids: set[str] | None,
    metrics: ExtractionMetrics,
) -> PageExtraction:
    """One structured VLM call for one page (plus the optional completeness
    retry), served from the on-disk cache when this exact page was already read."""
    key = cache.page_key(
        page_path, model, PROMPT_VERSION, question_ids, question_types, previous_question_id
    )
    cached = cache.load(key)
    if cached is not None:
        metrics.pages_cached += 1
        return cached

    prompt = (
        EXTRACTION_SYSTEM_PROMPT
        + "\n\n"
        + build_extraction_user_prompt(question_ids, question_types, previous_question_id)
    )
    parsed = generate_structured(client, model, prompt, PageExtraction, [page_path])
    assert isinstance(parsed, PageExtraction)
    parsed = _recover_missing(client, model, prompt, page_path, parsed, allowed_ids, metrics)
    cache.store(key, parsed)
    return parsed


def vlm_extract_questions(
    page_paths: list[Path],
    question_ids: list[str] | None = None,
    client: ModelClient | None = None,
    question_types: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, int], str, list[dict[str, Any]], dict[str, float]]:
    """Read full exam pages and return (question_id -> extracted answer,
    question_id -> 1-indexed page number, model, conflicts, question_id ->
    extraction confidence).

    `conflicts` lists everything a teacher should look at: duplicate question IDs
    that looked like misreads rather than continuations (stored under
    '{qid}__CONFLICT_pageN' keys), answers whose exact value was suspiciously
    repeated across many question IDs on one page ('{qid}__SUSPECT_DUPLICATE_pageN'),
    content the model couldn't attach to any valid question number
    ('Q__UNASSIGNED_pageN_k', each with a `suggested_question_id`), valid IDs that
    never received any entry across the whole script (given an empty-string
    placeholder), and answers the model marked illegible (entries with
    kind="illegible"; the answer itself is stored under its normal key).
    """
    if not page_paths:
        raise ModelUnavailable("No page images were provided for VLM extraction")

    if client is None:
        vlm_client, vlm_model = get_vlm_client_and_model()
    else:
        # An explicit client override (used by tests/callers that want full
        # control) is used as-is.
        vlm_client = client
        vlm_model = os.getenv("RUBRICTRACE_VLM_MODEL", "qwen2.5vl:3b")

    allowed_ids = {normalize_qid(q) for q in question_ids} if question_ids else None

    merged: dict[str, str] = {}
    pages: dict[str, int] = {}
    question_confidences: dict[str, float] = {}
    conflicts: list[dict[str, Any]] = []
    metrics = ExtractionMetrics(pages_total=len(page_paths))
    previous_qid: str | None = None

    print(
        f"    [extract] reading {len(page_paths)} page(s) with {vlm_model} (single pass)…",
        flush=True,
    )

    for page_number, page_path in enumerate(page_paths, start=1):
        blank = is_blank_page(page_path)
        if blank.is_blank:
            metrics.pages_blank_skipped += 1
            print(
                f"    [extract] page {page_number}/{len(page_paths)}: skipped as blank "
                f"({blank.reason}), no VLM call made",
                flush=True,
            )
            continue

        started = time.monotonic()
        parsed = _read_page(
            vlm_client,
            vlm_model,
            page_path,
            question_ids,
            question_types,
            previous_qid,
            allowed_ids,
            metrics,
        )
        elapsed = time.monotonic() - started
        if os.getenv("RUBRICTRACE_DEBUG_VLM"):
            print(
                f"    [extract][debug] page {page_number} raw_text: {parsed.raw_text}", flush=True
            )

        if not parsed.answers and not parsed.unassigned and not parsed.raw_text.strip():
            metrics.pages_no_content += 1
            print(
                f"    [extract] page {page_number}/{len(page_paths)}: model reported no "
                f"readable content ({elapsed:.1f}s), skipping",
                flush=True,
            )
            continue

        found: dict[str, str] = {}
        continuation_ids: set[str] = set()
        for answer in parsed.answers:
            qid = normalize_qid(answer.question_id)
            text = "" if answer.status == "blank" else answer.answer.strip()
            if qid not in found:
                found[qid] = text
            elif text:
                found[qid] = f"{found[qid]}\n{text}" if found[qid] else text
            if answer.continues_previous:
                continuation_ids.add(qid)

            confidence = answer.confidence
            if answer.status == "illegible":
                confidence = min(
                    confidence if confidence is not None else ILLEGIBLE_CONFIDENCE_CAP,
                    ILLEGIBLE_CONFIDENCE_CAP,
                )
                if allowed_ids is None or qid in allowed_ids:
                    metrics.illegible_items += 1
                    message = (
                        f"page {page_number}: the answer for {qid} could not be read "
                        "reliably from the page image"
                        + (f" (best reading: {text!r})" if text else "")
                        + "; check it against the source page."
                    )
                    print(f"    [extract][illegible][NEEDS REVIEW] {message}", flush=True)
                    conflicts.append(
                        {
                            "question_id": qid,
                            "conflict_key": None,
                            "kind": "illegible",
                            "page": page_number,
                            "other_missing": [],
                            "message": message,
                        }
                    )
            if answer.status != "blank" and confidence is not None and 0.0 <= confidence <= 1.0:
                previous = question_confidences.get(qid)
                question_confidences[qid] = (
                    confidence if previous is None else min(previous, confidence)
                )

        # PROMPT-QUALITY signal: catch same-page duplicate-value fabrication
        # (see extraction/review_flags.py) before it's trusted as real content.
        found, suspicious_groups = extract_suspicious_duplicates(found)
        suspicious_count = sum(len(qids) for _, qids in suspicious_groups)
        metrics.suspect_duplicate_groups += len(suspicious_groups)
        metrics.suspect_duplicate_items += suspicious_count

        conflicts_before = len(conflicts)
        merge_questions(merged, pages, found, page_number, allowed_ids, conflicts, continuation_ids)
        metrics.digit_conflict_items += len(conflicts) - conflicts_before

        if suspicious_groups:
            record_suspicious_duplicates(merged, pages, suspicious_groups, page_number, conflicts)

        if parsed.unassigned:
            unassigned_before = len(conflicts)
            record_unassigned(merged, pages, parsed.unassigned, page_number, conflicts, allowed_ids)
            metrics.unassigned_items += len(conflicts) - unassigned_before

        # Hint for the next page's continuation check: the last question that
        # actually has text on this page, in the order the model listed them.
        answered = [normalize_qid(a.question_id) for a in parsed.answers if a.answer.strip()]
        if answered and (allowed_ids is None or answered[-1] in allowed_ids):
            previous_qid = answered[-1]

        print(
            f"    [extract] page {page_number}/{len(page_paths)}: read in {elapsed:.1f}s "
            f"({len(found)} question(s) found"
            + (f", {len(parsed.unassigned)} unassigned" if parsed.unassigned else "")
            + (f", {suspicious_count} flagged as suspect duplicates" if suspicious_count else "")
            + ")",
            flush=True,
        )

    flagged = [c for c in conflicts if c.get("kind") != "illegible"]
    if flagged:
        print(
            f"    [extract] {len(flagged)} item(s) flagged for manual review "
            "(keys ending in __CONFLICT_pageN or __SUSPECT_DUPLICATE_pageN, or "
            "starting with Q__UNASSIGNED_pageN)",
            flush=True,
        )

    # PROMPT-QUALITY signal, checked once the whole script has been read: a valid
    # ID that never got any entry at all (not even an explicit blank) usually means
    # its content was misread onto a different question number. Must run after
    # every page, since a question absent on page 1 might still appear on page 3.
    never_mentioned = find_never_mentioned_ids(allowed_ids, merged)
    if never_mentioned:
        metrics.never_mentioned_items = len(never_mentioned)
        record_never_mentioned(merged, never_mentioned, conflicts)

    metrics.log_summary()

    return merged, pages, f"vlm:{vlm_model}", conflicts, question_confidences


def vlm_transcribe_extractions(
    extraction_paths: dict[str, Path],
    client: ModelClient | None = None,
) -> tuple[dict[str, str], str]:
    """Transcribe each per-question extraction image with the VLM."""
    if client is None:
        client, model = get_vlm_client_and_model()
    else:
        model = os.getenv("RUBRICTRACE_VLM_MODEL", "qwen2.5vl:3b")
    if not extraction_paths:
        return {}, model

    answers: dict[str, str] = {}

    for question_id, image_path in extraction_paths.items():
        qid = normalize_qid(question_id)
        prompt = (
            f"This image is the cropped region for {qid} from a student answer script. "
            "Transcribe the student's answer faithfully (print or handwriting). "
            "Do not grade. Do not add commentary. "
            f'Return JSON only: {{"question_id": "{qid}", "answer": "transcribed text"}}'
        )
        parsed = generate_structured(
            client, model, prompt, VlmExtractionTranscription, [image_path]
        )
        assert isinstance(parsed, VlmExtractionTranscription)
        answers[qid] = parsed.answer.strip()

    return answers, model
