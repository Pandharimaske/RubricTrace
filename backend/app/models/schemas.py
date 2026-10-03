from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RubricCriterion(BaseModel):
    name: str
    marks: float = Field(gt=0)
    expected_concepts: list[str] = Field(default_factory=list)


class ScoreRequest(BaseModel):
    question_id: str
    student_answer: str
    max_marks: float = Field(gt=0)
    criteria: list[RubricCriterion]


class CriterionScore(BaseModel):
    name: str
    awarded_marks: float
    max_marks: float
    matched_concepts: list[str]
    missing_concepts: list[str]
    evidence: str
    confidence: float


class ScoreResponse(BaseModel):
    question_id: str
    awarded_marks: float
    max_marks: float
    confidence: float
    needs_review: bool
    criteria: list[CriterionScore]
    llm_explanation: dict[str, object] | None = None
    llm_model: str | None = None


class GradingCriterion(BaseModel):
    name: str
    marks: float = Field(gt=0)
    expected_concepts: list[str] = Field(
        default_factory=list,
        description="Semantic key points for the LLM — not strings to keyword-match.",
    )
    guidance: str = ""


class QuestionOption(BaseModel):
    option_key: str = Field(min_length=1, max_length=20)
    option_text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False
    display_order: int = Field(default=0, ge=0)


class QuestionGradingConfig(BaseModel):
    question_id: str
    question_text: str = ""
    golden_answer: str = ""
    question_type: Literal["mcq", "true_false", "short_answer", "long_answer"] = "short_answer"
    max_marks: float = Field(gt=0)
    criteria: list[GradingCriterion] = Field(default_factory=list)
    options: list[QuestionOption] = Field(default_factory=list)
    review_confidence_threshold: float | None = Field(default=None, ge=0.05, le=1.0)
    evaluator_config_id: str | None = None

    @model_validator(mode="after")
    def _check_type_specific_fields(self) -> "QuestionGradingConfig":
        if self.question_type in {"mcq", "true_false"} and self.criteria:
            raise ValueError(f"{self.question_type} questions cannot have rubric criteria")
        if self.question_type in {"short_answer", "long_answer"} and not self.criteria:
            raise ValueError("Open-ended questions need at least one rubric criterion")
        if self.question_type == "mcq":
            if len(self.options) < 2 or sum(option.is_correct for option in self.options) != 1:
                raise ValueError("MCQ questions need at least two options and one correct option")
        elif self.question_type == "true_false":
            if len(self.options) not in {0, 2}:
                raise ValueError("True/false questions must have two options")
            if self.options and sum(option.is_correct for option in self.options) != 1:
                raise ValueError("True/false questions need one correct option")
        elif self.options:
            raise ValueError("Only MCQ and true/false questions can have options")
        return self


class EvaluatorConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    evaluator_kind: Literal["deterministic", "slm", "llm"]
    provider_name: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    fallback_config_id: str | None = None


class TeacherEvaluationRequest(BaseModel):
    script_id: str
    questions: list[QuestionGradingConfig]


class TeacherOverrideRequest(BaseModel):
    question_id: str
    awarded_marks: float = Field(ge=0)
    reason: str = ""


class ExamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ExamUpdate(BaseModel):
    """Rename an exam and/or replace its answer key + rubric."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    questions: list[QuestionGradingConfig] | None = None
    # Answers whose grading confidence is below this are flagged for teacher review.
    # Applied when reading, so changing it never needs a re-grade.
    review_confidence_threshold: float | None = Field(default=None, ge=0.05, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _normalize_sections_or_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if "exam_title" in payload and not payload.get("name"):
            payload["name"] = payload["exam_title"]
        if "sections" in payload and "questions" not in payload:
            flat_questions = []
            for section in payload.get("sections", []):
                stype = section.get("type") or section.get("question_type") or ""
                smarks = section.get("marks_per_question", 1)
                for q in section.get("questions", []):
                    raw_id = q.get("question_id") or q.get("id") or ""
                    qid = str(raw_id).strip()
                    if qid.isdigit():
                        qid = f"Q{qid}"

                    raw_type = str(
                        q.get("type") or q.get("question_type") or stype or "short_answer"
                    )
                    raw_type_clean = raw_type.lower().replace("-", "").replace("_", "")
                    if "mcq" in raw_type_clean or "multiple" in raw_type_clean:
                        qtype = "mcq"
                    elif "true" in raw_type_clean or "tf" in raw_type_clean:
                        qtype = "true_false"
                    elif "long" in raw_type_clean:
                        qtype = "long_answer"
                    else:
                        qtype = "short_answer"

                    golden = str(
                        q.get("golden_answer")
                        or q.get("correct_answer")
                        or q.get("model_answer")
                        or q.get("answer")
                        or ""
                    )
                    max_marks = float(q.get("max_marks") or smarks)

                    options = []
                    if qtype in {"mcq", "true_false"}:
                        raw_opts = q.get("options", {})
                        if isinstance(raw_opts, dict):
                            for idx, (k, v) in enumerate(raw_opts.items()):
                                is_corr = str(k).strip().upper() == golden.strip().upper()
                                options.append(
                                    {
                                        "option_key": str(k),
                                        "option_text": str(v),
                                        "is_correct": is_corr,
                                        "display_order": idx,
                                    }
                                )
                        elif isinstance(raw_opts, list):
                            for idx, opt in enumerate(raw_opts):
                                if isinstance(opt, str):
                                    key = chr(65 + idx)
                                    options.append(
                                        {
                                            "option_key": key,
                                            "option_text": opt,
                                            "is_correct": key == golden.strip().upper(),
                                            "display_order": idx,
                                        }
                                    )
                                elif isinstance(opt, dict):
                                    key = opt.get("option_key") or opt.get("key") or chr(65 + idx)
                                    options.append(
                                        {
                                            "option_key": key,
                                            "option_text": opt.get("option_text")
                                            or opt.get("text")
                                            or "",
                                            "is_correct": bool(
                                                opt.get("is_correct", key == golden.strip().upper())
                                            ),
                                            "display_order": idx,
                                        }
                                    )

                    criteria = []
                    if qtype not in {"mcq", "true_false"}:
                        raw_rubric = q.get("rubric") or q.get("criteria") or []
                        for idx, c in enumerate(raw_rubric):
                            text = str(c.get("criterion") or c.get("name") or "")
                            guidance = str(c.get("guidance") or "")
                            if " — " in text:
                                parts = text.split(" — ")
                                name = parts[0].strip()
                                if not guidance:
                                    guidance = parts[1].strip()
                            else:
                                name = text[:80].strip()
                                if not guidance and text != name:
                                    guidance = text

                            criteria.append(
                                {
                                    "name": name or f"Criterion {idx + 1}",
                                    "marks": float(c.get("points") or c.get("marks") or 1),
                                    "expected_concepts": c.get("expected_concepts") or [],
                                    "guidance": guidance,
                                }
                            )
                        if not criteria:
                            criteria.append(
                                {
                                    "name": "Correctness",
                                    "marks": max_marks,
                                    "expected_concepts": [],
                                    "guidance": "",
                                }
                            )

                    flat_questions.append(
                        {
                            "question_id": qid,
                            "question_text": q.get("question") or q.get("question_text") or "",
                            "question_type": qtype,
                            "golden_answer": golden,
                            "max_marks": max_marks,
                            "options": options,
                            "criteria": criteria,
                            "review_confidence_threshold": q.get("review_confidence_threshold"),
                            "evaluator_config_id": q.get("evaluator_config_id"),
                        }
                    )
            payload["questions"] = flat_questions
        return payload

    @model_validator(mode="after")
    def _check_questions(self) -> "ExamUpdate":
        if self.questions is None:
            return self
        seen: set[str] = set()
        for q in self.questions:
            qid = q.question_id.strip()
            if not qid:
                raise ValueError("Every question needs an ID")
            if qid in seen:
                raise ValueError(f"Duplicate question ID: {qid}")
            seen.add(qid)
            if q.criteria:
                total = round(sum(c.marks for c in q.criteria), 2)
                if abs(total - q.max_marks) > 0.01:
                    raise ValueError(
                        f"{qid}: criteria add up to {total} marks but max marks is {q.max_marks}"
                    )
        return self


class GradeRequest(BaseModel):
    # False: grade only ungraded questions. True: grade everything again.
    # Teacher-approved marks are never overwritten either way.
    regrade: bool = False
