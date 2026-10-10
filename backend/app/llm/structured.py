"""
Pydantic schemas for validating raw LLM/VLM JSON output, plus a validate-and-retry
wrapper around ModelClient.generate().

Why this exists: parse_json_response() (in parsing.py) only guarantees the response
is *some* JSON object — it says nothing about whether the right keys/types are
present. Cloud NIM models in particular will happily return syntactically valid
JSON that's missing a field, has the wrong type, or wraps things differently than
prompted. generate_structured() catches that at the schema level and gives the
model a chance to fix it, instead of failing deep in grader.py/pipeline.py
with a KeyError or a silently wrong type.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Literal

from backend.app.core.config import settings
from backend.app.llm.errors import ModelUnavailable
from backend.app.llm.parsing import parse_json_response
from backend.app.llm.provider import ModelClient
from pydantic import BaseModel, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

# ---- Output schemas -------------------------------------------------------


class PageAnswer(BaseModel):
    """One question's answer as read from a page image.

    status: "answered" (readable answer), "blank" (number visible, nothing
    written) or "illegible" (something written but not reliably readable —
    `answer` then holds a best partial reading, and the pipeline flags it for
    teacher review). continues_previous marks unnumbered text at the top of a
    page that continues the previous page's last answer.
    """

    question_id: str
    status: Literal["answered", "blank", "illegible"] = "answered"
    answer: str = ""
    confidence: float | None = None
    continues_previous: bool = False

    @field_validator("question_id", mode="before")
    @classmethod
    def _qid_to_str(cls, value: Any) -> str:
        return str(value)

    @field_validator("answer", mode="before")
    @classmethod
    def _answer_to_str(cls, value: Any) -> str:
        return "" if value is None else str(value)


class PageExtraction(BaseModel):
    """Shape returned by the single-pass page extraction call.

    `raw_text` comes first on purpose: the model reads the whole page into it
    before filling in `answers`, and it is kept for audit and cross-checked
    against `answers` (see extraction/pipeline.py).

    `unassigned` holds substantial answer-like content the model could not
    confidently attach to any valid question number. The pipeline stores each
    entry under a distinct 'Q__UNASSIGNED_pageN_k' key so a teacher sees it
    instead of it silently vanishing.

    Small models sometimes return `answers` as a {"Q1": "A"} mapping instead of
    a list, or null for an empty list; both are accepted rather than costing a
    retry.
    """

    raw_text: str = ""
    answers: list[PageAnswer] = Field(default_factory=list)
    unassigned: list[str] = Field(default_factory=list)

    @field_validator("raw_text", mode="before")
    @classmethod
    def _raw_text_to_str(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("answers", mode="before")
    @classmethod
    def _answers_to_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, dict):
            return [{"question_id": key, "answer": val} for key, val in value.items()]
        return value

    @field_validator("unassigned", mode="before")
    @classmethod
    def _unassigned_to_list(cls, value: Any) -> Any:
        return [] if value is None else value


class GraderOutput(BaseModel):
    """Shape returned by the grading prompt in grader.py. Deliberately no
    needs_review / review_reason here, same reasoning as MCQGraderOutput below:
    a model asked to fill 9+ fields at once (this schema has more than MCQ's)
    is even more prone to a self-contradictory needs_review than MCQ was --
    needs_review is derived from confidence in code instead (see
    _SHORT_ANSWER_REVIEW_CONFIDENCE in grader.py).
    """

    awarded_marks: float
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)
    concepts_found: list[str] = Field(default_factory=list)
    concepts_missing: list[str] = Field(default_factory=list)
    criteria_satisfied: list[str] = Field(default_factory=list)
    criteria_partial: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    criteria_scores: list[dict[str, object]] = Field(default_factory=list)


class MCQGraderOutput(BaseModel):
    """Shape returned by the lean MCQ-only grading prompt (llm_grade_mcq() in
    grader.py): short reasoning, the correct/incorrect verdict, and a confidence
    in that verdict. Deliberately no needs_review / review_reason here -- those
    are derived from confidence in code. A small local model fills separate
    fields independently, and a model-filled needs_review routinely contradicted
    its own confidence and reasoning (e.g. 0.95 confidence, reasoning saying the
    answer was clearly option D, yet flagged), flooding the review queue with
    false positives. Field order matters: reasoning comes first so the verdict
    follows from it.
    """

    reasoning: str = ""
    is_correct: bool
    confidence: float = 0.5


# ---- Validate-and-retry helper ---------------------------------------------


def parse_and_validate(raw: str, schema: type[BaseModel]) -> BaseModel:
    """Run the existing markdown-fence-stripping parser, then validate against schema."""
    payload = parse_json_response(raw)
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ModelUnavailable(f"Model output failed schema validation: {exc}") from exc


def generate_structured(
    client: ModelClient,
    model: str,
    prompt: str,
    schema: type[BaseModel],
    image_paths: list[Path] | None = None,
    max_retries: int | None = None,
) -> BaseModel:
    """
    Call client.generate(), parse the JSON, and validate it against `schema`.

    Retries (up to max_retries times) on two distinct failure modes:
      - the client itself raising ModelUnavailable (request failed, empty
        response, no choices returned) — usually a transient NIM/network
        hiccup that succeeds on a retry with the same prompt.
      - the response failing to parse or validate against `schema` —
        re-prompts with the validation error appended so the model can see
        exactly what was wrong and correct it.

    These used to be handled inconsistently: only the second case was
    retried, so a bare "empty response" from the API would crash the whole
    extraction run instead of retrying the way a malformed-JSON response does.

    max_retries defaults to RUBRICTRACE_STRUCTURED_MAX_RETRIES (env var) or 2.
    """
    if max_retries is None:
        max_retries = settings.structured_max_retries
    last_error: Exception | None = None
    current_prompt = prompt

    for attempt in range(max_retries + 1):
        try:
            raw = client.generate(model, current_prompt, image_paths, json_output=True)
        except ModelUnavailable as exc:
            last_error = exc
            if attempt < max_retries:
                wait = min(2**attempt, 5)
                log.warning(
                    "%s attempt %d request failed (%s), retrying in %ds",
                    model,
                    attempt + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)
            continue

        try:
            return parse_and_validate(raw, schema)
        except ModelUnavailable as exc:
            last_error = exc
            if attempt < max_retries:
                log.warning(
                    "%s attempt %d failed schema validation, retrying: %s",
                    model,
                    attempt + 1,
                    exc,
                )
            current_prompt = (
                prompt + f"\n\nYour previous response was invalid: {exc}\n"
                "Return corrected JSON only, matching the required shape exactly. "
                "No markdown, no commentary."
            )

    raise ModelUnavailable(
        f"{model} failed to produce valid structured output after "
        f"{max_retries} retries: {last_error}"
    )
