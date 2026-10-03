"""Pure merge logic for combining one page's structured results into the
script-wide question_id -> answer mapping. No model calls here — every
function takes plain dicts/strings and returns plain dicts/strings, which is
what makes this independently unit-testable without mocking an LLM.
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.services.assessment.extraction.segment import normalize_question_id


def normalize_qid(raw: str) -> str:
    return f"Q{normalize_question_id(str(raw))}"


# Phrases a model emits instead of an empty string when it can't find real
# content for a question. Not real answer content — must never end up in a
# stored answer.
NOT_VISIBLE_PATTERNS = (
    "student's answer is not visible",
    "student's answer is not present",
    "answer is not visible on this page",
    "not visible on this page",
    "not present on this page",
    "no answer is visible",
    "cannot see the student's answer",
    "question is not visible on this page",
    "question does not appear on this page",
    "not found in the transcript",
    "no content found",
)


def strip_not_visible_noise(text: str) -> str:
    """Remove placeholder 'not visible' sentences from otherwise-real text.

    A model sometimes appends a placeholder sentence to real content in the
    same response. Strip any sentence containing one of these patterns rather
    than discarding the whole value, so genuine answer text sitting alongside
    the noise isn't lost.
    """
    if not text:
        return text
    lowered = text.lower()
    if not any(pattern in lowered for pattern in NOT_VISIBLE_PATTERNS):
        return text

    chunks = re.split(r"(?<=[.\n])\s*", text)
    kept = [
        chunk
        for chunk in chunks
        if chunk.strip() and not any(p in chunk.lower() for p in NOT_VISIBLE_PATTERNS)
    ]
    return " ".join(kept).strip()


def is_placeholder_only(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(pattern in lowered for pattern in NOT_VISIBLE_PATTERNS) and len(text) < 120


def merge_questions(
    target: dict[str, str],
    pages: dict[str, int],
    incoming: dict[str, Any],
    page_number: int,
    allowed_ids: set[str] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    continuation_ids: set[str] | None = None,
) -> None:
    """Merge one page's {question_id: answer} result into `target` in place.

    A duplicate question ID that the model explicitly marked as a continuation
    of the previous page's answer (`continuation_ids`) is always appended.
    Otherwise, a duplicate is only auto-merged (concatenated) if every other
    hint ID has already been filled — i.e. it reads as a genuine multi-page
    continuation of the same answer. Otherwise it's far more likely a misread
    digit colliding with a different, still-missing question, so it's stored
    under a separate '{qid}__CONFLICT_pageN' key and appended to `conflicts`
    for manual review instead of being silently concatenated.
    """
    if not isinstance(incoming, dict):
        return

    # Snapshot of hint IDs still unfilled *before* this page's answers are
    # applied. Used below to tell "genuine multi-page continuation of the same
    # question" (nothing else missing, safe to merge) apart from "this
    # duplicate ID is probably a misread of a different, still-missing
    # question" (flag instead of silently merging).
    missing_before_page = (allowed_ids - target.keys()) if allowed_ids is not None else set()

    for key, value in incoming.items():
        qid = normalize_qid(key)

        # A hint list means we told the model exactly which question IDs
        # exist on this exam. A qid outside that set is essentially always a
        # misread of a real number rather than a genuinely different
        # question, so accepting it just pollutes the output with a
        # nonexistent question. Drop it rather than merging it.
        if allowed_ids is not None and qid not in allowed_ids:
            print(
                f"    [extract][merge] page {page_number}: dropping {qid} "
                f"(not in this exam's question list)",
                flush=True,
            )
            continue

        raw_text = str(value).strip()
        if raw_text and is_placeholder_only(raw_text):
            continue
        text = strip_not_visible_noise(raw_text)

        existing = target.get(qid, "")
        if existing and text and text not in existing:
            if continuation_ids and qid in continuation_ids:
                print(
                    f"    [extract][merge] page {page_number}: {qid} continues its "
                    f"answer from page {pages.get(qid, '?')} — appending (marked as a "
                    "continuation by the model).",
                    flush=True,
                )
                target[qid] = f"{existing}\n{text}"
                continue
            other_missing = missing_before_page - {qid}
            if other_missing:
                # Other hint IDs are still completely empty. A duplicate
                # showing up here is far more likely a misread digit (e.g. 28
                # vs 29) than a genuine continuation, and concatenating would
                # garble both answers together. Store this page's content
                # under a distinct, clearly-marked key instead, so it
                # surfaces for manual review rather than silently corrupting
                # two questions at once.
                conflict_qid = f"{qid}__CONFLICT_page{page_number}"
                target[conflict_qid] = text
                pages[conflict_qid] = page_number
                message = (
                    f"{qid} already had an answer from page {pages.get(qid, '?')}, "
                    f"but {sorted(other_missing)} still has no answer at all — this "
                    "looks like a misread question number rather than a real "
                    f"continuation. Stored separately as '{conflict_qid}' instead "
                    "of merging; needs manual review."
                )
                print(
                    f"    [extract][merge][NEEDS REVIEW] page {page_number}: {message}",
                    flush=True,
                )
                if conflicts is not None:
                    conflicts.append(
                        {
                            "question_id": qid,
                            "conflict_key": conflict_qid,
                            "page": page_number,
                            "other_missing": sorted(other_missing),
                            "message": message,
                        }
                    )
                continue

            print(
                f"    [extract][merge] page {page_number}: {qid} already had an "
                f"answer from page {pages.get(qid, '?')} — appending new content "
                "from this page too. No other hint IDs are missing, so this reads "
                "as a genuine multi-page continuation.",
                flush=True,
            )
            target[qid] = f"{existing}\n{text}"
        elif qid not in target or text:
            target[qid] = text
        if text and qid not in pages:
            pages[qid] = page_number
