#!/usr/bin/env python3
"""
Build a single consolidated extraction-results index from the per-student VLM
extraction files in data/raw/extracted_answers/ (student1.json, student2.json,
...), in the {"summary":..., "results":[...]} shape grade_mcq_local.py and
grade_short_answer_cloud.py's --extracted flag expects (student_id,
question_id, extracted_answer per record — see _load_extracted_answers() in
both scripts).

Why this exists: the two grading scripts previously defaulted to
data/processed/mendeley_sf3kvjwknt_results.json, which turned out to be
stale output from the ABANDONED tesseract/regex-crop extraction approach
(89.7% blank extracted_answer across all 1750 records — tesseract blind-OCR'd
scanned handwritten pages, predictably producing near-nothing). The real,
current extraction lives here instead: one Claude-Sonnet-5-produced JSON file
per student, each a list of pages, each page a list of per-question
extractions. This script flattens all of that into the flat per-question
record shape the rest of the grading pipeline already expects, so nothing
downstream (grader.py, compute_grading_metrics.py) needs to change.

Input file shape (data/raw/extracted_answers/student<N>.json):
    [
      {
        "page_number": 1,
        "extractions": [
          {"question_number": "1", "answer_text": "D", "is_partial": false,
           "confidence": "high", "notes": "MCQ"},
          ...
        ]
      },
      ...
    ]

Input
-----
--dir      Directory of per-student extraction files. Defaults to
           data/raw/extracted_answers.
--pattern  Filename pattern for the student number. Defaults to
           student(\\d+)\\.json — matches student1.json, student27.json, etc.
           Files that don't match (e.g. .DS_Store) are silently skipped.

Output
------
--output   Consolidated {"summary":..., "results":[...]} JSON. Defaults to
           data/processed/extracted_answers_index.json.

Edge cases handled explicitly:
  - filename doesn't match the student<N>.json pattern (e.g. .DS_Store,
    a README): skipped silently, not an error.
  - a file that isn't valid JSON, or isn't the expected
    list-of-pages-of-extractions shape: reported as a per-file error and
    skipped, run continues for the other students (a single corrupt file
    shouldn't kill the whole index build).
  - the same question_number appears more than once for one student (across
    pages, or twice on one page): this is the expected shape of a student
    running out of room and continuing an answer onto the next page — the
    extraction re-emits the same question_number on the next page's
    extractions rather than losing the overflow. CONCATENATED (earlier text
    + "\n" + later text, in page order), not overwritten, and every such case
    is reported (see "concatenated_answers" in the summary) so it can be
    spot-checked against the source PDF. page_number is kept from the FIRST
    occurrence (where the answer starts). confidence is the more
    conservative (lower) of the two chunks' labels, since a heuristically
    stitched answer deserves more scrutiny than either chunk alone;
    is_partial and notes are taken from the LAST chunk, since that's the one
    that determines whether the now-combined answer is still incomplete.
  - answer_text is "" (a real "left unanswered" case per this dataset's own
    "notes" field, not an extraction failure): kept as "" — downstream
    graders already treat blank as blank and award 0 with needs_review,
    which is the correct outcome for a genuinely unanswered question too.
    An empty chunk concatenated with a non-empty one just contributes
    nothing to the merge (no stray leading/trailing space).
  - question_number written with non-numeric characters or leading zeros
    (e.g. "07"): normalized to int and re-rendered as "Q7" so it matches the
    manifest's Q<N> question_id convention; entries that don't parse as an
    integer at all are kept with a "Q<raw text>" id and flagged in the
    "unparsed_question_numbers" summary field for manual inspection instead
    of being silently dropped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.settings import PROCESSED_DIR, RAW_DIR

DEFAULT_INPUT_DIR = RAW_DIR / "extracted_answers"
DEFAULT_PATTERN = re.compile(r"^student(\d+)\.json$", re.IGNORECASE)

# Ordered worst-to-best-ish for picking "the more conservative of two labels"
# on a concatenated (heuristically stitched) answer. Anything not in this map
# (an unexpected label) ranks below all three known ones, so it wins the "more
# conservative" comparison and surfaces rather than being silently ignored.
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _question_id(raw: str) -> tuple[str, bool]:
    """Returns (question_id, was_unparsed)."""
    stripped = raw.strip()
    try:
        return f"Q{int(stripped)}", False
    except ValueError:
        return f"Q{stripped}", True


def _load_student_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"expected a JSON list of pages, got {type(payload).__name__}")
    return payload


def _lower_confidence(a: str, b: str) -> str:
    return a if _CONFIDENCE_RANK.get(a, -1) <= _CONFIDENCE_RANK.get(b, -1) else b


def _concatenate_answer(earlier: str, later: str) -> str:
    """Joins with a newline rather than a space: this dataset's multi-part
    answers are almost always bullet lists ("- Classifier 1: ..."), and a
    newline preserves that structure when the next bullet lands right at the
    top of the following page. For an answer that instead continues mid-
    sentence across the page break, a newline still reads fine to the LLM
    grader (which cares about content, not exact prose formatting) — unlike a
    plain space, which actively mangles the more common bulleted case."""
    earlier, later = earlier.strip(), later.strip()
    if earlier and later:
        return f"{earlier}\n{later}"
    return earlier or later


def build_index(
    input_dir: Path, pattern: re.Pattern
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    file_errors: list[str] = []
    concatenations: list[str] = []
    unparsed: list[str] = []

    student_files = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and pattern.match(p.name)),
        key=lambda p: int(pattern.match(p.name).group(1)),
    )
    if not student_files:
        raise SystemExit(
            f"No files matching {pattern.pattern!r} found in {input_dir}. Check --dir / --pattern."
        )

    for path in student_files:
        student_num = pattern.match(path.name).group(1)
        student_id = f"Student_{student_num}"

        try:
            pages = _load_student_file(path)
        except (json.JSONDecodeError, ValueError) as exc:
            file_errors.append(f"{path.name}: {exc}")
            continue

        by_question: dict[str, dict[str, Any]] = {}
        for page in pages:
            page_number = page.get("page_number")
            for item in page.get("extractions", []):
                raw_qnum = str(item.get("question_number", "")).strip()
                if not raw_qnum:
                    continue
                question_id, was_unparsed = _question_id(raw_qnum)
                if was_unparsed:
                    unparsed.append(
                        f"{student_id} {question_id} (raw: {raw_qnum!r}) in {path.name}"
                    )

                new_answer = str(item.get("answer_text", "") or "")
                new_confidence = item.get("confidence", "")
                new_notes = item.get("notes", "")
                new_is_partial = bool(item.get("is_partial", False))

                existing = by_question.get(question_id)
                if existing is not None:
                    concatenations.append(
                        f"{student_id} {question_id}: page {existing['page_number']} "
                        f"+ page {page_number} (concatenated, kept page {existing['page_number']})"
                    )
                    by_question[question_id] = {
                        "student_id": student_id,
                        "question_id": question_id,
                        "extracted_answer": _concatenate_answer(
                            existing["extracted_answer"], new_answer
                        ),
                        "is_partial": new_is_partial,
                        "extraction_confidence_label": _lower_confidence(
                            existing["extraction_confidence_label"], new_confidence
                        ),
                        "notes": new_notes,
                        "page_number": existing["page_number"],  # first occurrence wins
                    }
                else:
                    by_question[question_id] = {
                        "student_id": student_id,
                        "question_id": question_id,
                        "extracted_answer": new_answer,
                        "is_partial": new_is_partial,
                        "extraction_confidence_label": new_confidence,
                        "notes": new_notes,
                        "page_number": page_number,
                    }

        records.extend(by_question.values())

    meta = {
        "students_indexed": len({r["student_id"] for r in records}),
        "files_with_errors": file_errors,
        "concatenated_answers": concatenations,
        "unparsed_question_numbers": unparsed,
    }
    return records, meta


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN.pattern,
        help=r"Regex for student filenames, with the student number as group 1 "
        r"(default: student(\d+)\.json).",
    )
    parser.add_argument(
        "--output", type=Path, default=PROCESSED_DIR / "extracted_answers_index.json"
    )
    args = parser.parse_args()

    pattern = re.compile(args.pattern, re.IGNORECASE)
    records, meta = build_index(args.dir, pattern)

    blank = sum(1 for r in records if not r["extracted_answer"].strip())
    summary = {
        "records": len(records),
        "blank": blank,
        "non_blank": len(records) - blank,
        **meta,
    }
    payload = {"summary": summary, "results": records}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    if meta["files_with_errors"]:
        print("\nFiles skipped due to errors:")
        for e in meta["files_with_errors"]:
            print(f"  {e}")
    if meta["concatenated_answers"]:
        print(f"\n{len(meta['concatenated_answers'])} answer(s) concatenated across pages:")
        for c in meta["concatenated_answers"]:
            print(f"  {c}")
    if meta["unparsed_question_numbers"]:
        print(f"\n{len(meta['unparsed_question_numbers'])} non-numeric question_number value(s):")
        for u in meta["unparsed_question_numbers"]:
            print(f"  {u}")

    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
