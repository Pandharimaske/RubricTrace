"""
LLM grader for RubricTrace.

The LLM is the only scoring engine. It compares the student answer to the
golden/model answer and teacher rubric, then returns marks plus reasoning.

Two entry points:
  llm_grade()      — full grading with reasoning/evidence/concepts, used for
                      descriptive (short/long answer) questions where partial
                      credit and an audit trail matter.
  llm_grade_mcq()   — lean grading for objective MCQ/true-false questions. The
                      model sees the question, the correct option and the
                      student's answer, and returns short reasoning, whether
                      the answer is correct, and a confidence.

Neither entry point returns needs_review. The model is never asked for it (a
model-filled flag kept contradicting its own confidence); it is derived from the
stored confidence and the exam's review threshold by the caller -- see
backend/app/db/database.py (review_verdict / effective_status).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from backend.app.llm.provider import ModelClient, get_llm_client_and_model
from backend.app.llm.structured import (
    GraderOutput,
    MCQGraderOutput,
    generate_structured,
)

_OPTION_LINE_RE = re.compile(r"(?m)^[ \t]*([A-Za-z])[\.\)][ \t]*(.+?)[ \t]*$")


def _parse_mcq_options(question_text: str) -> dict[str, str]:
    """Pull an option-letter -> option-text mapping out of the QUESTION's own
    text block (format: 'A. ...\nB. ...\n...', authored consistently by the
    manifest/exam builder -- fixed, machine-generated structure).

    This deliberately never touches the student's answer: a student's
    handwritten/transcribed response varies too much in format for any fixed
    pattern to safely interpret, which is exactly why matching it is left to
    the LLM (see _MCQ_SYSTEM_PROMPT). Parsing the question's own option list
    is a different, much safer job -- it's always authored in this one
    consistent layout -- and doing it here in code means the golden answer can
    be handed to the model already resolved to both its letter AND its full
    option text, instead of asking the model to also do that lookup itself.

    Returns {} if the text doesn't look like an options list (e.g. non-MCQ).
    """
    options: dict[str, str] = {}
    for match in _OPTION_LINE_RE.finditer(question_text or ""):
        letter, text = match.group(1).upper(), match.group(2).strip()
        if letter in options or not text:
            continue
        options[letter] = text
    return options


_SYSTEM_PROMPT = """\
You are an expert exam grader. You are the scoring engine — not a helper that
explains someone else's score.

You receive:
- the question ID and, when available, its question type (MCQ, true/false,
  short answer, or long answer)
- the question that was asked (for MCQ this includes the option list, A-D)
- the student's answer, exactly as transcribed from the script
- the golden / model answer
- a rubric (criteria, marks, key points, grading rules)
- the maximum marks

Always read the question first and grade the student's answer as a response to
that specific question — not just as text similar to the golden answer. If
the student answers a different question, or only partially addresses what
was asked, that matters even if their text otherwise resembles the golden
answer.

Grade by meaning, not by keyword search. Credit equivalent phrasing, correct
reasoning, and demonstrated understanding. Do not award marks just because a
word from the rubric appears. Do not deduct marks just because the student
used different wording, units, notation, or ordering than the golden answer.

How to grade by question type:
- MCQ (objective, single correct option): the student's answer may be written
  many different ways — a bare letter ("C"), the question number plus letter
  ("1. C"), the letter plus the option's own text ("C. Little or no
  autocorrelation"), or only the option's text with no letter at all. Read the
  option list in the question text and judge which option the student meant,
  by meaning, not by exact string match. This is objective: award full marks
  if the student clearly indicates the correct option, zero marks otherwise —
  there is no partial credit unless the rubric explicitly says so. If the
  answer is genuinely ambiguous between two options, or names none of them,
  award zero and give low confidence.
- True/false: the student's answer may be "T"/"F", "True"/"False",
  "Yes"/"No", a checkmark description, or a full sentence that implies one or
  the other. Judge by meaning; this is objective, full or zero marks unless
  the rubric says otherwise.
- Short answer / long answer (descriptive): grade against the rubric criteria
  and golden answer, giving partial credit per criterion where the rubric
  allows it. Longer answers may bury the relevant point among extra,
  tangential, or repeated material — grade the substance that answers the
  question and ignore filler; do not penalize length or wordiness itself.
  - If the question asks for a specific NUMBER of examples ("list any two...",
    "name one..."), and the rubric's expected_concepts lists MORE alternatives
    than that number, treat those concepts as a pool of acceptable examples —
    not a checklist the student must fully cover. Award full marks for any
    correct, relevant answer(s) meeting the asked-for count, even if the
    student's examples are not the specific ones named in expected_concepts,
    as long as they genuinely satisfy what was asked.
  - When a single rubric criterion bundles several sub-concepts from the
    golden answer (expected_concepts listing more items than the criterion
    describes), award credit in proportion to the meaningfully-covered
    fraction, rounded to the nearest allowed mark — do not default to 0 just
    because the exact expected phrase or term is missing. A student who
    clearly engages with the right topic and shows partial, imprecise, or
    incomplete understanding should receive some credit, not zero, unless the
    answer is off-topic, substantively wrong, or shows no relevant
    understanding at all.
  - When a rubric criterion's wording includes a qualifier like "specific"
    (e.g. "gives a specific real-world application"), do not read that as
    requiring the same granularity as the golden/model answer's own examples.
    A correctly-named broader category (a named domain, sector, or use-case
    family) satisfies such a criterion as long as it is a genuine, valid
    instance of what was asked — "specific" distinguishes a real, named thing
    from a vague or generic statement ("technology", "it helps businesses"),
    not a request for maximum granularity. Only require finer granularity
    than the student gave if the question or an explicit guidance note says
    so.

Edge cases you will encounter — handle each explicitly rather than guessing:
- Empty or whitespace-only student answer: you should rarely see this — it's
  handled before reaching you — but if you do, award 0 marks and use low
  confidence; do not assume the student meant something.
- Garbled, truncated, or partially unreadable transcription (a common OCR/VLM
  artifact): grade whatever meaningful content is present; if the garbling
  makes it impossible to judge correctness, award 0 with low confidence.
- Student answer that reads as a coherent, well-formed response to a
  DIFFERENT question than the one asked (a sign of upstream mislabeling, not a
  wrong answer): do not force-fit it against this question's rubric — award 0
  with low confidence, and say so in reasoning so a teacher can check for a
  labeling error.
- Missing golden answer and/or empty rubric: fall back to your own subject
  knowledge of what a correct answer to the stated question would contain,
  say so in reasoning, and use low confidence since there is nothing to
  verify against.
- Multiple valid ways to express the same correct answer (different notation,
  units, synonyms, equivalent examples): treat them as equally correct.
- Student answer that is a superset of the correct answer (correct point plus
  extra unrelated or incorrect material): credit the correct part per the
  rubric; only deduct for the extra material if the rubric or question
  explicitly penalizes incorrect additions.

Rules:
- Do NOT award marks for vague, off-topic, or unsupported statements.
- Be fair when the student shows clear understanding with imperfect phrasing.
- Awarded marks must be between 0 and max_marks inclusive.
- Express confidence (0.0-1.0) in how sure you are that YOUR awarded_marks is
  right. A teacher reviews low-confidence answers, so confidence is the only
  signal that decides that -- treat it as a real, calibrated probability, not
  a formality:
  - Give HIGH confidence (0.9+) to a clear-cut call: the answer plainly does
    or doesn't cover what the rubric asks for.
  - Give LOWER confidence (below 0.65) when you are genuinely unsure: the
    answer is ambiguous, handwriting/transcription looks incomplete, the
    answer seems to belong to a different question, or you're not sure the
    rubric even applies as written.
  - When your mark depends on judging PARTIAL or proportional concept
    coverage rather than a clear-cut correct/incorrect call -- including
    deciding how many of several listed expected_concepts a free-form answer
    actually covers, or awarding 0 or full marks on that kind of subjective
    coverage judgment -- that call is inherently less certain than an
    objective check. Reflect that with confidence below 0.9.
- In reasoning, cite the golden answer and rubric: what matched, what was
  missing, and why each portion of marks was given or withheld.
- Return ONLY valid JSON. No markdown, no extra text.

JSON schema (return exactly this shape):
{
  "awarded_marks": <number>,
  "reasoning": "<step-by-step: for each criterion, what from the golden answer / rubric was present or missing, and how many marks>",
  "evidence": ["<phrase from the student answer that supports a mark decision>"],
  "concepts_found": ["<key point from the rubric/golden answer the student covered>"],
  "concepts_missing": ["<key point the student did not cover>"],
  "criteria_satisfied": ["<criterion name fully satisfied>"],
  "criteria_partial": ["<criterion name partially satisfied>"],
  "criteria_failed": ["<criterion name not satisfied>"],
  "criteria_scores": [
    {
      "criterion": "<criterion name>",
      "awarded_marks": <number>,
      "max_marks": <number>,
      "confidence": <0.0-1.0>,
      "evidence": ["<supporting phrase>"],
      "reasoning": "<criterion-specific explanation>"
    }
  ],
  "confidence": <0.0-1.0>
}
"""


_MCQ_SYSTEM_PROMPT = """\
You are an expert exam grader scoring a single objective multiple-choice (or
true/false) question. You are the scoring engine.

You receive: the question text with its option list (A-D, or True/False), the
correct option (golden answer), and the student's answer exactly as
transcribed from their script.

The student's answer may be written in many forms:
- a bare option letter, e.g. "C"
- the question number plus letter, e.g. "1. C"
- the letter plus the option's own text, e.g. "C. Little or no autocorrelation"
- only the option's text, with no letter at all
- "True"/"False", "T"/"F", "Yes"/"No", or a sentence implying one of those
Work out which option the student chose by meaning, using the option list, then
compare it with the correct option. This is objective: there is no partial
credit. The student is either correct or incorrect.

Steps:
1. Identify the option the student chose (if any).
2. Compare it with the correct option.
3. Give your verdict and confidence.

How to set confidence (0.0-1.0): it is how sure you are that YOUR VERDICT is
right, not whether the student is right.
- A clearly chosen option gets HIGH confidence (0.9 or above) whether it is
  correct or wrong. A student who clearly picked D when the key is B is simply
  wrong -- that is a confident, ordinary verdict, not a reason to doubt
  yourself.
- Lower it (below 0.65) only when you genuinely cannot tell what the student
  chose: the answer is empty or unreadable, it marks two or more different
  options, it matches none of the listed options, or it seems to belong to a
  different question. In those cases the verdict is is_correct=false.
- A letter followed by that same option's own text is one clear choice, not
  two.

Base everything on the actual student answer text given to you. Do not call an
answer blank or unreadable unless it truly is.

reasoning: one or two short sentences stating which option the student chose,
which option is correct, and why the verdict follows.

Return ONLY valid JSON, no markdown, no extra text, with the keys in this
order:
{
  "reasoning": "<one or two short sentences>",
  "is_correct": <true|false>,
  "confidence": <0.0-1.0>
}
"""


def _build_prompt(
    question_id: str,
    question_text: str,
    student_answer: str,
    golden_answer: str,
    rubric: list[dict[str, Any]],
    max_marks: float,
    guidance: str = "",
    question_type: str = "",
) -> str:
    rubric_text = json.dumps(rubric, indent=2)
    guidance_block = f"\nAdditional grading rules from the teacher:\n{guidance}" if guidance else ""
    type_line = (
        f"Question type: {question_type}\n"
        if question_type
        else "Question type: not specified — infer from the question text and rubric "
        "whether this is MCQ, true/false, short answer, or long answer.\n"
    )
    return (
        f"Question ID: {question_id}\n"
        f"{type_line}"
        f"Maximum marks: {max_marks}\n\n"
        f"Question asked:\n{question_text or '(not provided)'}\n\n"
        f"Golden/model answer:\n{golden_answer or '(not provided)'}\n\n"
        "Rubric (interpret key points semantically; do not string-match them):\n"
        f"{rubric_text}\n"
        f"{guidance_block}\n\n"
        f"Student answer:\n{student_answer or '(empty)'}\n\n"
        "Grade the student answer against the question, the golden answer and the rubric. "
        "Return the JSON result."
    )


def _build_mcq_prompt(
    question_id: str,
    question_text: str,
    student_answer: str,
    golden_answer: str,
) -> str:
    options = _parse_mcq_options(question_text)
    golden_letter = (golden_answer or "").strip().upper()
    golden_text = options.get(golden_letter, "")

    if golden_text:
        golden_line = f'Golden answer: option {golden_letter} — "{golden_text}"'
        golden_note = (
            "(the correct option is given above by BOTH its letter and its full "
            "option text -- match the student's answer against either form)\n"
        )
    else:
        # Question text didn't parse as a clean options list (e.g. non-MCQ
        # slipped in, or unusual formatting) -- fall back to the bare letter
        # rather than silently claiming a resolved option that doesn't exist.
        golden_line = f"Golden answer (correct option letter): {golden_answer or '(not provided)'}"
        golden_note = ""

    options_block = (
        "\n".join(f"{letter}. {text}" for letter, text in sorted(options.items()))
        if options
        else "(could not be parsed separately -- read them from the question text below)"
    )

    return (
        f"Question ID: {question_id}\n\n"
        f"Question asked:\n{question_text or '(not provided)'}\n\n"
        f"Options (parsed from the question above):\n{options_block}\n\n"
        f"{golden_line}\n"
        f"{golden_note}\n"
        f"Student answer, exactly as transcribed:\n{student_answer or '(empty)'}\n\n"
        "Judge which option the student meant and whether it matches the golden answer. "
        "Return the JSON result."
    )


def llm_grade(
    question_id: str,
    student_answer: str,
    golden_answer: str,
    rubric: list[dict[str, Any]],
    max_marks: float,
    guidance: str = "",
    question_text: str = "",
    question_type: str = "",
    client: ModelClient | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Grade a student answer with the LLM (full reasoning — GraderOutput shape).

    Parameters
    ----------
    client, model : pass both together to pin grading to a specific backend/model
        regardless of the global RUBRICTRACE_MODEL_PROVIDER — e.g. a batch script
        that wants short-answer grading on Groq/NVIDIA while a sibling process
        grades MCQs locally via llm_grade_mcq(). If client is given without model,
        this falls back to RUBRICTRACE_LLM_MODEL (or "qwen2.5:3b"), which is almost
        never what you want for a non-Ollama client — pass model explicitly.
        If both are omitted, the provider/model come from get_llm_client_and_model()
        (i.e. RUBRICTRACE_MODEL_PROVIDER).

    Returns
    -------
    (grading_result_dict, model_name)

    Raises
    ------
    ModelUnavailable  if the configured model backend is unreachable or returns
    invalid output (local Ollama, or NVIDIA's/Groq's cloud API — see provider.py).
    """
    if client is None:
        client, model = get_llm_client_and_model()
    elif model is None:
        model = os.getenv("RUBRICTRACE_LLM_MODEL", "qwen2.5:3b")

    full_prompt = (
        _SYSTEM_PROMPT
        + "\n\n---\n\n"
        + _build_prompt(
            question_id,
            question_text,
            student_answer,
            golden_answer,
            rubric,
            max_marks,
            guidance,
            question_type,
        )
    )

    parsed = generate_structured(client, model, full_prompt, GraderOutput)
    result = parsed.model_dump()

    awarded = max(0.0, min(result["awarded_marks"], max_marks))
    result["awarded_marks"] = round(awarded, 2)

    confidence = max(0.0, min(result["confidence"], 1.0))
    result["confidence"] = round(confidence, 2)

    # needs_review is not decided here: only confidence is returned. The caller
    # judges it against the exam's review threshold (see module docstring).

    return result, model


def llm_grade_mcq(
    question_id: str,
    student_answer: str,
    golden_answer: str,
    question_text: str = "",
    client: ModelClient | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Grade a single objective MCQ/true-false question (lean — MCQGraderOutput
    shape: correct-or-not, no reasoning chain). Intended for a small, fast,
    local model (qwen2.5:3b by default) since the task is a single semantic
    match, not open-ended judgment — see _MCQ_SYSTEM_PROMPT.

    Same client/model override behavior as llm_grade(): pass both to pin this
    to a specific backend regardless of the global provider setting.

    Returns
    -------
    (grading_result_dict, model_name) — grading_result_dict has keys
    is_correct, confidence, reasoning. No needs_review: derive it from confidence
    against the exam's review threshold (database.review_verdict).

    Raises
    ------
    ModelUnavailable  if the configured model backend is unreachable or returns
    invalid output.
    """
    if client is None:
        client, model = get_llm_client_and_model()
    elif model is None:
        model = os.getenv("RUBRICTRACE_LLM_MODEL", "qwen2.5:3b")

    full_prompt = (
        _MCQ_SYSTEM_PROMPT
        + "\n\n---\n\n"
        + _build_mcq_prompt(question_id, question_text, student_answer, golden_answer)
    )

    parsed = generate_structured(client, model, full_prompt, MCQGraderOutput)

    confidence = round(max(0.0, min(float(parsed.confidence), 1.0)), 2)

    # needs_review is not decided here: only confidence is returned (see module docstring).

    return {
        "is_correct": bool(parsed.is_correct),
        "confidence": confidence,
        "reasoning": (parsed.reasoning or "").strip(),
    }, model
