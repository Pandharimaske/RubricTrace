"""Pure validation/flagging logic for the extraction pipeline. No model calls
here — every function takes plain dicts/strings and returns plain
dicts/strings, which is what makes this independently unit-testable without
mocking an LLM.

Four checks live here:

  numbers_mentioned_in_transcript()   A cheap regex completeness check: does
                                       this page's raw transcript appear to
                                       mention a question number that the
                                       model's answers list is
                                       missing? Used only as a signal to
                                       retry the page once, never to
                                       silently correct anything on its own.

  extract_suspicious_duplicates()     Same-page fabrication detection: the
                                       identical answer value repeated across
                                       an unusual number of distinct question
                                       IDs on ONE page. Real distinct
                                       questions essentially never share one
                                       exact non-trivial answer string many
                                       times over — this instead matches what
                                       a transcription VLM does when it can't
                                       actually read a dense/illegible
                                       section and fabricates the same
                                       plausible-looking value for every item
                                       in it (see the '1044' repeated across
                                       ten question numbers case in
                                       the retired transcription prompt's worked
                                       example).

  record_unassigned() /
  suggest_sequential_ids()            Content the model flagged
                                       as real but couldn't attach to any
                                       valid question number (typically a page
                                       with no visible numbering at all).
                                       Stored for manual review rather than
                                       guessed at, with a `suggested_question_id`
                                       — the next still-unfilled valid ID in
                                       ascending order — attached purely as a
                                       reviewer's starting point. That
                                       suggestion is NEVER written in as the
                                       actual answer and NEVER trusted
                                       automatically, since a page's true
                                       answer order isn't guaranteed to match
                                       numeric ID order.

  find_never_mentioned_ids() /
  record_never_mentioned()            A valid question ID that never got ANY
                                       entry at all across the whole script —
                                       not even an explicit empty string for
                                       "blank on this page". Different from a
                                       genuinely blank answer (which does get
                                       an empty-string entry once the model
                                       sees its number) and from the other
                                       checks above (which all involve some
                                       trace of the ID appearing somewhere).
                                       A fully absent ID after the whole
                                       script has been processed usually means
                                       its content was misread onto a
                                       different question number and merged
                                       there instead of being recognized as
                                       its own answer (see the shape-based
                                       digit-matching constraint in
                                       extraction/prompt.py) — run once at
                                       the end of vlm_extract_questions, after
                                       every page has been processed.
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.extraction.merge import normalize_qid

_NUMBERED_ITEM = re.compile(r"(?im)^\s*\*{0,2}(?:question\s*)?(\d{1,3})\s*[.):]")


def numbers_mentioned_in_transcript(page_text: str, allowed_ids: set[str] | None) -> set[str]:
    """Question numbers that appear to start a line in the raw transcript,
    normalized to Qn form. A cheap regex, not authoritative (transcript
    formatting varies) — used only as a coarse completeness check on the
    answers list, i.e. a signal to retry, never to silently correct
    anything on its own."""
    found = set()
    for match in _NUMBERED_ITEM.finditer(page_text):
        qid = normalize_qid(match.group(1))
        if allowed_ids is None or qid in allowed_ids:
            found.add(qid)
    return found


_SUSPICIOUS_DUPLICATE_MIN_COUNT = 3
_SUSPICIOUS_DUPLICATE_MIN_LENGTH = 2
# Legitimately repeated across many questions (true/false and yes/no items).
_SUSPICIOUS_DUPLICATE_EXEMPT = frozenset({"true", "false", "yes", "no"})


def extract_suspicious_duplicates(
    found: dict[str, str],
) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """Split off answers that look like transcription fabrication rather than
    real per-question content: the identical value repeated verbatim across
    an unusual number of distinct question IDs on the SAME page. Short values
    (single MCQ letters/digits like 'A' or '3') are exempted, since genuine
    repetition there is completely normal and expected.

    Returns (clean, groups) where `clean` is `found` with any flagged entries
    removed, and `groups` is a list of (repeated_value, {qid: text}) for each
    flagged cluster.
    """
    by_value: dict[str, dict[str, str]] = {}
    for qid, text in found.items():
        normalized = str(text).strip()
        if (
            len(normalized) < _SUSPICIOUS_DUPLICATE_MIN_LENGTH
            or normalized.lower() in _SUSPICIOUS_DUPLICATE_EXEMPT
        ):
            continue
        by_value.setdefault(normalized, {})[qid] = text

    groups = [
        (value, qids)
        for value, qids in by_value.items()
        if len(qids) >= _SUSPICIOUS_DUPLICATE_MIN_COUNT
    ]
    if not groups:
        return found, []

    flagged_qids = {qid for _, qids in groups for qid in qids}
    clean = {qid: text for qid, text in found.items() if qid not in flagged_qids}
    return clean, groups


def record_suspicious_duplicates(
    target: dict[str, str],
    pages: dict[str, int],
    groups: list[tuple[str, dict[str, str]]],
    page_number: int,
    conflicts: list[dict[str, Any]] | None = None,
) -> None:
    """Stash answers whose value was suspiciously repeated across many
    distinct question IDs on the same page (see extract_suspicious_duplicates
    above) under '{qid}__SUSPECT_DUPLICATE_pageN' keys instead of trusting
    them as real per-question content, so a teacher checks the source page
    image before any of them counts as an actual answer."""
    for value, qids in groups:
        for qid, text in qids.items():
            key = f"{qid}__SUSPECT_DUPLICATE_page{page_number}"
            target[key] = text
            pages[key] = page_number
        message = (
            f"page {page_number}: {sorted(qids.keys())} were all transcribed "
            f"with the identical value {value!r} — this looks like the "
            "model fabricating one repeated placeholder for a "
            f"section it couldn't actually read, rather than {len(qids)} "
            "genuinely identical answers. Stored under "
            "'__SUSPECT_DUPLICATE_pageN' keys instead of the real question IDs; "
            "needs manual review against the source page image."
        )
        print(f"    [extract][suspect-duplicate][NEEDS REVIEW] {message}", flush=True)
        if conflicts is not None:
            conflicts.append(
                {
                    "question_id": None,
                    "conflict_key": None,
                    "page": page_number,
                    "other_missing": sorted(qids.keys()),
                    "message": message,
                }
            )


def suggest_sequential_ids(
    allowed_ids: set[str] | None,
    target: dict[str, str],
    count: int,
) -> list[str | None]:
    """Best-guess candidates for `count` consecutive unassigned items on a
    page with no visible numbering at all: the next `count` still-missing
    valid question IDs, in ascending numeric order. Purely a starting point
    for manual review — never written in as the actual answer, and never
    trusted automatically, since a page's true answer order isn't guaranteed
    to match numeric ID order."""
    if not allowed_ids or count <= 0:
        return [None] * count

    def _num(qid: str) -> int:
        try:
            return int(qid[1:])
        except ValueError:
            return 10**9

    filled = {qid for qid in target if "__" not in qid}
    remaining = sorted(allowed_ids - filled, key=_num)
    return [remaining[i] if i < len(remaining) else None for i in range(count)]


def record_unassigned(
    target: dict[str, str],
    pages: dict[str, int],
    unassigned: list[str],
    page_number: int,
    conflicts: list[dict[str, Any]] | None = None,
    allowed_ids: set[str] | None = None,
) -> None:
    """Stash content the structuring model flagged as real but unattachable
    to any valid question number, so it's visible for manual review instead
    of disappearing. Mirrors the '__CONFLICT_pageN' pattern used for
    misread-digit collisions in merge.py, under '__UNASSIGNED_pageN_k' keys.

    Each entry also gets a `suggested_question_id` (see suggest_sequential_ids
    above) — a starting point for whoever reviews it manually, never trusted
    automatically."""
    real_items = [str(t).strip() for t in unassigned if str(t).strip()]
    suggestions = suggest_sequential_ids(allowed_ids, target, len(real_items))

    idx = 0
    for raw_text in unassigned:
        text = str(raw_text).strip()
        if not text:
            continue
        idx += 1
        key = f"Q__UNASSIGNED_page{page_number}_{idx}"
        target[key] = text
        pages[key] = page_number
        suggestion = suggestions[idx - 1]
        suggestion_note = (
            f" A plausible starting guess — the next still-unfilled question "
            f"number at this point, since this page has no visible numbering — "
            f"is {suggestion}; confirm against the source page image before "
            "accepting it."
            if suggestion
            else ""
        )
        message = (
            f"page {page_number} has answer-like content that the "
            "model could not confidently attach to any valid question number "
            f"(no number visible, or an unrecognized one). Stored as '{key}'; "
            "needs manual review and assignment to the correct question."
            f"{suggestion_note}"
        )
        print(f"    [extract][unassigned][NEEDS REVIEW] {message}", flush=True)
        if conflicts is not None:
            conflicts.append(
                {
                    "question_id": None,
                    "conflict_key": key,
                    "page": page_number,
                    "other_missing": [],
                    "message": message,
                    "suggested_question_id": suggestion,
                }
            )


def find_never_mentioned_ids(allowed_ids: set[str] | None, target: dict[str, str]) -> list[str]:
    """Valid question IDs that have no entry at all in `target` — not a real
    answer, not an explicit empty string, not a '__CONFLICT'/'__SUSPECT'/
    '__UNASSIGNED' entry either. Call this once, after every page in the
    script has been processed, not per-page: a question absent from page 1
    might still turn up on page 3.

    This genuinely differs from every other case handled in this module —
    those all involve some trace of the ID appearing somewhere (a real
    answer, an explicit blank, or flagged content). An ID with NO trace
    anywhere is a stronger signal that something went wrong upstream (most
    likely: its content got misread onto a different question number and
    absorbed into that other question's answer during merge) rather than the
    student simply leaving it blank, which would still produce an explicit
    empty-string entry once the model recognizes the number."""
    if not allowed_ids:
        return []

    def _num(qid: str) -> int:
        try:
            return int(qid[1:])
        except ValueError:
            return 10**9

    return sorted(allowed_ids - target.keys(), key=_num)


def record_never_mentioned(
    target: dict[str, str],
    never_mentioned: list[str],
    conflicts: list[dict[str, Any]] | None = None,
) -> None:
    """Flag valid question IDs that never appeared anywhere in the script (see
    find_never_mentioned_ids above) by writing an explicit empty-string entry
    for each — so it's visible in the final answer set rather than silently
    absent — and recording one review message covering all of them."""
    if not never_mentioned:
        return
    for qid in never_mentioned:
        target[qid] = ""
    message = (
        f"{never_mentioned} never appeared anywhere in this script's "
        "transcripts or structured output, on any page — not even as an "
        "explicit blank. This is different from a question the student "
        "genuinely left blank (which gets an empty-string entry as soon as "
        "the model recognizes its number): a completely absent ID usually "
        "means its content was misread as a different, similarly-shaped "
        "number and merged into that other question's answer instead (check "
        "whether a numerically nearby or shape-similar question's answer "
        "above looks like it might actually belong here). Recorded with an "
        "empty placeholder for now; needs manual review against the source "
        "page images."
    )
    print(f"    [extract][never-mentioned][NEEDS REVIEW] {message}", flush=True)
    if conflicts is not None:
        conflicts.append(
            {
                "question_id": None,
                "conflict_key": None,
                "page": None,
                "other_missing": never_mentioned,
                "message": message,
            }
        )
