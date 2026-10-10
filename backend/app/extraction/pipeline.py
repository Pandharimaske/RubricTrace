"""Single-pass VLM extraction: one structured call per page image -> merge into one
question_id -> answer mapping.

Each page goes to the VLM once. The model returns a transcript (`raw_text`) plus the page's
answers attached to question numbers, in one JSON object, with the exam's question IDs and
types in the prompt. There is no separate text-LLM structuring stage: the old two-stage flow
lost layout cues (circled/ticked MCQ options, strikeouts, continuations) between stages, and a
digit misread in stage one became stage two's ground truth.

This is the only module in the extraction pipeline that talks to a model client. Prompt text
lives in extraction/prompt.py; pure merge/validation logic lives in extraction/merge.py and
extraction/review_flags.py; per-run counters live in extraction/metrics.py; the per-page result
cache lives in extraction/cache.py.

Safety nets, and where each one lives:
  - a question ID outside the exam's expected list is dropped, not silently merged in
    (merge.py)
  - a blank/near-blank page never reaches the VLM at all, so it can't be a source of
    hallucinated content (blank_page.py)
  - a page the model reports as empty (no raw_text, no answers) is skipped
  - an answer the model marks "illegible" is stored with its best partial reading, capped at
    low confidence, and flagged for teacher review (this module)
  - a question number that appears in the model's own raw_text but has no entry in its answers
    list triggers one retry naming the gap (this module)
  - a duplicate question ID across pages is only auto-merged if the model marked it a
    continuation, or every other expected ID is already filled; otherwise it's flagged as a
    conflict for manual review (merge.py)
  - an answer value repeated identically across an unusual number of distinct question IDs on
    one page (a sign of one fabricated value for an illegible section) is caught before merge
    and stored separately (review_flags.py)
  - substantial content that can't be attached to any question number is surfaced as an
    unassigned entry with a `suggested_question_id` best guess (review_flags.py)
  - a valid question ID that never received any entry across the whole script is flagged once
    every page has been processed (review_flags.py)

The first two bullets are "structural" (some information genuinely isn't recoverable from a page
image); the rest are "prompt-quality" signals expected to fire less as the prompt improves. See
extraction/metrics.py.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from backend.app.core.config import Settings, settings
from backend.app.extraction.blank_page import is_blank_page
from backend.app.extraction.cache import ExtractionCache
from backend.app.extraction.merge import merge_questions, normalize_qid
from backend.app.extraction.metrics import ExtractionMetrics
from backend.app.extraction.prompt import (
    EXTRACTION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    build_extraction_user_prompt,
)
from backend.app.extraction.review_flags import (
    extract_suspicious_duplicates,
    find_never_mentioned_ids,
    numbers_mentioned_in_transcript,
    record_never_mentioned,
    record_suspicious_duplicates,
    record_unassigned,
)
from backend.app.llm.errors import ModelUnavailable
from backend.app.llm.provider import ModelClient, ModelProvider
from backend.app.llm.structured import PageExtraction, generate_structured

log = logging.getLogger(__name__)

# An answer the model marked illegible never counts as confident, whatever number it reported.
ILLEGIBLE_CONFIDENCE_CAP = 0.3


@dataclass
class ExtractionOutcome:
    """Everything one script's extraction produced.

    `conflicts` lists everything a teacher should look at: duplicate question IDs that looked
    like misreads rather than continuations (stored under '{qid}__CONFLICT_pageN' keys),
    answers whose exact value was suspiciously repeated across many question IDs on one page
    ('{qid}__SUSPECT_DUPLICATE_pageN'), content the model couldn't attach to any valid
    question number ('Q__UNASSIGNED_pageN_k', each with a `suggested_question_id`), valid IDs
    that never received any entry across the whole script (given an empty-string placeholder),
    and answers the model marked illegible (kind="illegible"; the answer itself is stored under
    its normal key).
    """

    questions: dict[str, str]
    question_pages: dict[str, int]
    model: str
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    question_confidences: dict[str, float] = field(default_factory=dict)


class ScriptExtractor:
    """Reads a script's page images with a VLM and merges them into one answer set."""

    def __init__(
        self,
        vlm_client: ModelClient | None = None,
        vlm_model: str | None = None,
        use_cache: bool | None = None,
        config: Settings | None = None,
    ) -> None:
        self.config = config or settings
        self.vlm_client = vlm_client
        self.vlm_model = vlm_model
        self.cache = ExtractionCache(self.config, use_cache)

    def resolve(self) -> tuple[ModelClient, str]:
        """The injected client/model, else the NVIDIA vision model. An injected client
        without a model name uses the configured vision model."""
        if self.vlm_client is None:
            client, default_model = ModelProvider(self.config).vlm()
            return client, self.vlm_model or default_model
        return self.vlm_client, self.vlm_model or self.config.nvidia_vlm_model

    # ── One page ─────────────────────────────────────────────────────────────

    @staticmethod
    def _recover_missing(
        client: ModelClient,
        model: str,
        prompt: str,
        page_path: Path,
        parsed: PageExtraction,
        allowed_ids: set[str] | None,
        metrics: ExtractionMetrics,
    ) -> PageExtraction:
        """PROMPT-QUALITY check: if the model's own raw_text mentions a question number that
        its answers list omits, retry once naming exactly what to double-check. Only the
        answers for the missing numbers are taken from the retry."""
        found_ids = {normalize_qid(a.question_id) for a in parsed.answers}
        gap = numbers_mentioned_in_transcript(parsed.raw_text, allowed_ids) - found_ids
        if not gap:
            return parsed

        metrics.completeness_retries_triggered += 1
        log.info(
            "raw_text mentions %s but the answers list doesn't include them; retrying with a "
            "completeness nudge",
            sorted(gap),
        )
        nudge_prompt = prompt + (
            "\n\nBefore finalizing, look at the page image again: your raw_text mentions "
            f"question number(s) {sorted(gap)} but your answers list has no entry for "
            "them. If the page has real answer content (or a visibly blank answer space) "
            "for any of them, add an entry. If on closer look one genuinely isn't on "
            "this page, leave it out. Return the complete JSON again."
        )
        try:
            retry = cast(
                PageExtraction,
                generate_structured(client, model, nudge_prompt, PageExtraction, [page_path]),
            )
        except ModelUnavailable as exc:
            log.warning("completeness retry failed (%s), keeping original result", exc)
            return parsed

        recovered = [a for a in retry.answers if normalize_qid(a.question_id) in gap]
        if recovered:
            metrics.completeness_retries_recovered += 1
            log.info(
                "recovered %s on completeness retry",
                sorted({normalize_qid(a.question_id) for a in recovered}),
            )
            parsed.answers.extend(recovered)
        return parsed

    def _read_page(
        self,
        client: ModelClient,
        model: str,
        page_path: Path,
        question_ids: list[str] | None,
        question_types: dict[str, str] | None,
        previous_question_id: str | None,
        allowed_ids: set[str] | None,
        metrics: ExtractionMetrics,
    ) -> PageExtraction:
        """One structured VLM call for one page (plus the optional completeness retry), served
        from the on-disk cache when this exact page was already read."""
        key = None
        if self.cache.enabled:
            key = self.cache.key(
                page_path, model, PROMPT_VERSION, question_ids, question_types, previous_question_id
            )
            cached = self.cache.load(key)
            if cached is not None:
                metrics.pages_cached += 1
                return cached

        prompt = (
            EXTRACTION_SYSTEM_PROMPT
            + "\n\n"
            + build_extraction_user_prompt(question_ids, question_types, previous_question_id)
        )
        parsed = cast(
            PageExtraction,
            generate_structured(client, model, prompt, PageExtraction, [page_path]),
        )
        parsed = self._recover_missing(
            client, model, prompt, page_path, parsed, allowed_ids, metrics
        )
        if key is not None:
            self.cache.store(key, parsed)
        return parsed

    @staticmethod
    def _collect_answers(
        parsed: PageExtraction,
        page_number: int,
        allowed_ids: set[str] | None,
        metrics: ExtractionMetrics,
        confidences: dict[str, float],
        conflicts: list[dict[str, Any]],
    ) -> tuple[dict[str, str], set[str]]:
        """Fold one page's answer entries into {qid: text}, record confidences, and report
        illegible answers. Returns (found, ids the model marked as continuations)."""
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
                    log.warning("[illegible][NEEDS REVIEW] %s", message)
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
                previous = confidences.get(qid)
                confidences[qid] = confidence if previous is None else min(previous, confidence)
        return found, continuation_ids

    # ── A whole script ───────────────────────────────────────────────────────

    def extract_pages(
        self,
        page_paths: list[Path],
        question_ids: list[str] | None = None,
        question_types: dict[str, str] | None = None,
    ) -> ExtractionOutcome:
        if not page_paths:
            raise ModelUnavailable("No page images were provided for VLM extraction")

        client, model = self.resolve()
        allowed_ids = {normalize_qid(q) for q in question_ids} if question_ids else None

        merged: dict[str, str] = {}
        pages: dict[str, int] = {}
        confidences: dict[str, float] = {}
        conflicts: list[dict[str, Any]] = []
        metrics = ExtractionMetrics(pages_total=len(page_paths))
        previous_qid: str | None = None

        log.info("reading %d page(s) with %s (single pass)", len(page_paths), model)

        for page_number, page_path in enumerate(page_paths, start=1):
            blank = is_blank_page(page_path)
            if blank.is_blank:
                metrics.pages_blank_skipped += 1
                log.info(
                    "page %d/%d skipped as blank (%s), no VLM call made",
                    page_number,
                    len(page_paths),
                    blank.reason,
                )
                continue

            started = time.monotonic()
            parsed = self._read_page(
                client,
                model,
                page_path,
                question_ids,
                question_types,
                previous_qid,
                allowed_ids,
                metrics,
            )
            elapsed = time.monotonic() - started
            if self.config.debug_vlm:
                log.debug("page %d raw_text: %s", page_number, parsed.raw_text)

            if not parsed.answers and not parsed.unassigned and not parsed.raw_text.strip():
                metrics.pages_no_content += 1
                log.info(
                    "page %d/%d: model reported no readable content (%.1fs), skipping",
                    page_number,
                    len(page_paths),
                    elapsed,
                )
                continue

            found, continuation_ids = self._collect_answers(
                parsed, page_number, allowed_ids, metrics, confidences, conflicts
            )

            # PROMPT-QUALITY signal: catch same-page duplicate-value fabrication (see
            # review_flags.py) before it's trusted as real content.
            found, suspicious_groups = extract_suspicious_duplicates(found)
            suspicious_count = sum(len(qids) for _, qids in suspicious_groups)
            metrics.suspect_duplicate_groups += len(suspicious_groups)
            metrics.suspect_duplicate_items += suspicious_count

            conflicts_before = len(conflicts)
            merge_questions(
                merged, pages, found, page_number, allowed_ids, conflicts, continuation_ids
            )
            metrics.digit_conflict_items += len(conflicts) - conflicts_before

            if suspicious_groups:
                record_suspicious_duplicates(
                    merged, pages, suspicious_groups, page_number, conflicts
                )

            if parsed.unassigned:
                unassigned_before = len(conflicts)
                record_unassigned(
                    merged, pages, parsed.unassigned, page_number, conflicts, allowed_ids
                )
                metrics.unassigned_items += len(conflicts) - unassigned_before

            # Hint for the next page's continuation check: the last question that actually has
            # text on this page, in the order the model listed them.
            answered = [normalize_qid(a.question_id) for a in parsed.answers if a.answer.strip()]
            if answered and (allowed_ids is None or answered[-1] in allowed_ids):
                previous_qid = answered[-1]

            log.info(
                "page %d/%d read in %.1fs (%d question(s) found, %d unassigned, %d suspect "
                "duplicates)",
                page_number,
                len(page_paths),
                elapsed,
                len(found),
                len(parsed.unassigned),
                suspicious_count,
            )

        flagged = [c for c in conflicts if c.get("kind") != "illegible"]
        if flagged:
            log.warning(
                "%d item(s) flagged for manual review (keys ending in __CONFLICT_pageN or "
                "__SUSPECT_DUPLICATE_pageN, or starting with Q__UNASSIGNED_pageN)",
                len(flagged),
            )

        # PROMPT-QUALITY signal, checked once the whole script has been read: a valid ID that
        # never got any entry at all (not even an explicit blank) usually means its content was
        # misread onto a different question number. Must run after every page, since a
        # question absent on page 1 might still appear on page 3.
        never_mentioned = find_never_mentioned_ids(allowed_ids, merged)
        if never_mentioned:
            metrics.never_mentioned_items = len(never_mentioned)
            record_never_mentioned(merged, never_mentioned, conflicts)

        metrics.log_summary()
        return ExtractionOutcome(merged, pages, f"vlm:{model}", conflicts, confidences)
