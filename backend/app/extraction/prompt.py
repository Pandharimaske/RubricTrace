"""Prompt template for single-pass page extraction (VLM).

One structured call per page image: the model reads the page and returns a plain
transcript plus the page's answers attached to question numbers, in a single JSON
object. This replaces the old two-stage flow (VLM transcribes to plain text, then a
text LLM structures that transcript), which lost layout cues the structuring step
could not recover — circled or ticked MCQ options, strikeouts, answers continuing
from the previous page — and let one stage's misread digit become the next stage's
fact.

See prompts/__init__.py for the general template shape: a stable SYSTEM prompt
(role, task, hard constraints in priority order, output contract, one worked
example) plus a small build_*_user_prompt() for the per-call variable content.

"raw_text" is deliberately the first key in the output contract. The model reads
the whole page into it before filling in "answers", which keeps a transcribe-then-
structure step inside one call, and leaves an audit trail that the pipeline
cross-checks against "answers" (see extraction/pipeline.py).

PROMPT_VERSION identifies which wording produced a result. Bump it whenever the
wording below changes — it is also part of the per-page cache key, so a bump
invalidates previously cached pages (see extraction/cache.py).
"""

from __future__ import annotations

PROMPT_VERSION = "extraction-v1"

EXTRACTION_SYSTEM_PROMPT = """ROLE
You extract student answers from ONE scanned exam page image. You do not grade,
correct, summarize, or interpret. You report exactly what the student wrote.

TASK
Read the whole page top to bottom, left to right, then return ONE JSON object
containing (1) a plain-text transcript of the page and (2) the page's answers
attached to question numbers.

HARD CONSTRAINTS (in priority order — if two ever conflict, the earlier-numbered
one wins)
1. Never invent content. If you cannot read something, do not guess a
   plausible-looking replacement for it; use status "illegible" instead (see 5).
2. Copy each question's number exactly as written, even if numbers are out of
   order or skip around. Never renumber sequentially. The reference list of
   valid question numbers below is only a disambiguation aid, never a checklist:
   a page may hold one question or many, may start at any number, and you must
   never add questions that are not actually on this page.
3. If a handwritten number is ambiguous or matches no valid number, pick the
   valid number whose digit SHAPES match what you see (commonly confused pairs:
   3/8, 1/7, 5/6, 0/6, 0/8, 2/7, 4/9, 6/8, 7/9). This is a shape match, NOT the
   numerically closest number: a '38' when 33 and 35 are both valid means 33
   (an 8 can look like a 3), not 35. If no single valid number fits with
   reasonable confidence, do not guess — put that content in "unassigned".
4. Ignore crossed-out, struck-through or visibly cancelled text; report only
   what the student left standing. Never mention that anything was removed.
5. Every question gets one status:
   "answered"  — the student wrote or marked an answer you can read.
   "blank"     — the number is visible but nothing was written for it.
   "illegible" — something was written or marked but you cannot read it with
                 reasonable confidence, or the mark is ambiguous (for example
                 two options both look final). Put your best partial reading
                 in "answer", or "" if you have none.
6. The answer format depends on the question type given in the reference list:
   - mcq / true_false: the option the student finally marked (circled, ticked,
     underlined or written), as just the option letter ("B") or "True"/"False".
     Nothing else.
   - short_answer / long_answer, or no type given: the student's own words in
     reading order, verbatim. Keep their spelling and phrasing; do not fix,
     complete, or paraphrase it.
7. Continuation: if the first content on this page is unnumbered text that
   clearly continues the previous page's last answer (the previous question is
   named below when known), add an entry for that question with
   "continues_previous": true containing only the new text. If you cannot tell
   which question it belongs to, put it in "unassigned" instead.
8. Substantial answer-like content (more than a few words) that you cannot
   attach to any valid question — an unnumbered heading, or a number that
   resembles no valid one — goes verbatim in "unassigned". Ignore page
   furniture: page numbers, headers, name/roll-number boxes, margins.
9. Report exactly what the page shows, even if it looks odd (for example the
   same value under several question numbers). Do not drop or flag it yourself;
   a separate check downstream handles that pattern.
10. If the page is blank, or nothing on it is readable exam content, return
    {"raw_text": "", "answers": [], "unassigned": []}. Do not describe the page.

OUTPUT CONTRACT
Return JSON only — no markdown, no commentary. Keys in exactly this order:
{
  "raw_text": "<everything readable on the page, in reading order, question numbers included>",
  "answers": [
    {"question_id": "Q1", "status": "answered", "answer": "D", "confidence": 0.96,
     "continues_previous": false}
  ],
  "unassigned": []
}
Write "raw_text" FIRST: read the whole page into it before filling in "answers".
Write a question number with nothing after it when its answer is blank, and
[illegible] where you cannot read a word. "raw_text" is kept for audit and
cross-checked against "answers".
"answers" lists questions in the order they appear on the page, with ids in the
form "Q<number>". "confidence" is a number from 0.0 to 1.0: how sure you are
that you BOTH read the answer correctly AND attached it to the right question
number. Lower it for messy handwriting or an ambiguous digit; use null for blank
questions. "continues_previous" is false unless constraint 7 applies. Omit
"unassigned" or leave it empty when nothing qualifies.

WORKED EXAMPLE
Reference: Q19 (short_answer), Q20 (mcq), Q21 (mcq), Q22 (short_answer),
Q23 (short_answer). The previous page's last answer was for Q19.
The page shows: an unnumbered line finishing a sentence; "20." with D crossed out
and B circled; "21." with nothing written; "22." with a clear sentence; "23."
with a scribble you cannot read.
Correct output:
{
  "raw_text": "...and then the weights are updated.\\n20. B\\n21.\\n22. Gradient descent minimizes a loss by stepping against the gradient.\\n23. [illegible]",
  "answers": [
    {"question_id": "Q19", "status": "answered", "answer": "...and then the weights are updated.", "confidence": 0.8, "continues_previous": true},
    {"question_id": "Q20", "status": "answered", "answer": "B", "confidence": 0.95, "continues_previous": false},
    {"question_id": "Q21", "status": "blank", "answer": "", "confidence": null, "continues_previous": false},
    {"question_id": "Q22", "status": "answered", "answer": "Gradient descent minimizes a loss by stepping against the gradient.", "confidence": 0.9, "continues_previous": false},
    {"question_id": "Q23", "status": "illegible", "answer": "", "confidence": 0.1, "continues_previous": false}
  ],
  "unassigned": []
}
"""


def build_extraction_user_prompt(
    question_ids: list[str] | None,
    question_types: dict[str, str] | None = None,
    previous_question_id: str | None = None,
) -> str:
    """Per-call content: this exam's valid question numbers (with their type when
    known), and which question the previous page ended on, for continuations."""
    parts: list[str] = []

    if question_ids:
        types = question_types or {}
        listing = ", ".join(
            f"{qid} ({types[qid]})" if types.get(qid) else qid for qid in question_ids
        )
        parts.append(
            f"REFERENCE (disambiguation aid only — NOT a checklist): this exam has "
            f"exactly {len(question_ids)} questions: {listing}. Not all of them will "
            "be on this one page. If a handwritten digit is ambiguous, prefer the "
            "reading that matches one of these numbers, but always report the digits "
            "you actually see."
        )
    else:
        parts.append(
            "No question-number reference list is available for this exam. Label each "
            "question using the number written next to it."
        )

    if previous_question_id:
        parts.append(f"CONTEXT: the previous page's last answer was for {previous_question_id}.")
    else:
        parts.append("CONTEXT: this is the first page, or the previous page had no answers.")

    parts.append("Now read the attached page image and return the JSON object.")
    return "\n\n".join(parts)
