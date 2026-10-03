# RubricTrace Session Handoff

Date: 2026-10-01

Use this document as context for the next development session.

## Product

RubricTrace is a teacher-in-the-loop exam grading system:

1. Teacher creates an exam with questions, golden answers, rubrics, marks, question types, evaluator/model choices, and confidence thresholds.
2. Teacher uploads one answer PDF or image per student.
3. The backend extracts question-level answers and metadata from each page using VLM/OCR workflows.
4. Each answer is graded using the configured evaluator.
5. The system stores marks, criterion scores, evidence, reasoning, confidence, and review flags.
6. The teacher reviews low-confidence answers and can approve or override marks.
7. Final results and evaluation metrics are produced later.

The original architecture diagram is available as `docs/rubrictrace_architecture_figure.pdf`.

## Repository Structure

The repository was reorganized so application code is separated cleanly:

```text
backend/
  app/
    api/
      routers/
        health.py
        students.py
        exams.py
        scripts.py
        grading.py
        rubrics.py
        review.py
    core/
    db/
    models/
    services/
  data/
  deploy/
  eval/
  scripts/
  tests/
  pyproject.toml
  uv.lock

frontend/
  src/
  package.json
  vite.config.js

docs/
tests were consolidated into backend/tests/
```

Root-level duplicate Python manifests were removed. Backend owns Python dependencies through `backend/pyproject.toml` and `backend/uv.lock`.

Backend-owned operational folders:

- `backend/data`
- `backend/deploy`
- `backend/eval`
- `backend/scripts`
- `backend/.env.example`

## Backend Work Completed

### Exam authoring schema

The old `exams.config_json` format remains synchronized for compatibility, but normalized tables are now used for authoring:

- `exams`
- `exam_questions`
- `question_options`
- `rubric_criteria`
- `evaluator_configs`

Supported question types:

- `mcq`
- `true_false`
- `short_answer`
- `long_answer`

Each question supports:

- question text
- golden answer
- maximum marks
- rubric criteria
- objective answer options
- evaluator configuration
- optional question-level confidence threshold

Threshold precedence:

```text
question.review_confidence_threshold
or exam.review_confidence_threshold
```

Exam question IDs are scoped per exam. Internally, questions use a generated row ID so `Q1` can safely exist in multiple exams.

### Evaluator configuration

Evaluator configurations support:

- `deterministic`
- `slm`
- `llm`

They store provider, model, temperature, max tokens, and optional fallback configuration.

Endpoints:

```text
GET  /api/exams/evaluator-configs
POST /api/exams/evaluator-configs
```

### Extraction metadata

The original `question_crops` concept was removed because the system no longer creates per-question crops. The canonical table is now `question_extractions`.

Stored metadata includes:

- `extraction_id`
- `script_id`
- `question_id`
- `question_number`
- `question_type`
- `page_number`
- `extracted_text`
- `extraction_method`
- `ocr_confidence`
- `vlm_confidence`
- `extraction_confidence`
- `needs_review`
- `review_reason`

Existing databases migrate from `question_crops` to `question_extractions` automatically. Legacy rows are copied safely and orphaned rows are skipped so startup does not fail.

Canonical endpoint:

```text
GET /api/scripts/{script_id}/extractions
```

The old `/api/scripts/{script_id}/crops` route remains as an undocumented compatibility alias only.

Extraction now uses page numbers rather than crop images. VLM structured extraction supports optional per-question extraction confidence. `vlm_confidence` remains nullable until the provider supplies a genuine calibrated value.

### Grading persistence

`question_evaluations` now stores:

- student answer text
- question type
- golden answer snapshot
- rubric snapshot
- awarded marks
- confidence
- threshold used
- needs-review state
- evidence
- reasoning
- raw LLM reasoning JSON
- criterion-level scores
- evaluator/model snapshot
- teacher override fields

The grader output contract supports criterion-level scores such as:

```json
{
  "criterion": "Accuracy",
  "awarded_marks": 1.5,
  "max_marks": 2,
  "confidence": 0.82,
  "evidence": ["..."],
  "reasoning": "..."
}
```

Review status calculations use the evaluation's stored `threshold_used`, not only the exam default.

## API Router Organization

The old monolithic API router was split into domain routers under `backend/app/api/routers/`:

- `health.py`: `/api/health`, `/api/stats`
- `students.py`: `/api/students`
- `exams.py`: exam authoring, evaluator configs, processing/grade jobs, results, CSV export
- `scripts.py`: upload, extraction, script details, page images, deletion
- `grading.py`: evaluation, quick scoring, teacher overrides
- `rubrics.py`: reusable rubric templates
- `review.py`: `/api/review-queue`

Existing endpoint URLs were preserved. The public OpenAPI contract currently exposes 27 API paths.

## Frontend Work Completed

The existing React/Vite demo frontend was retained and upgraded rather than replaced wholesale.

It currently has:

- React 19
- React Router
- TanStack React Query
- Lucide icons
- Vite
- Exam workspace workflow
- Exam authoring UI
- Student script upload UI
- Processing and grading job UI
- Review queue UI
- Results UI

The exam authoring editor now supports:

- short answer
- long answer
- MCQ
- true/false
- per-question confidence threshold override
- evaluator selection
- MCQ/true-false options
- correct-answer selection
- frontend validation matching backend rules

The frontend now uses `extractions` terminology instead of `crops` for script results and details.

## Validation Completed

Repeated checks have passed:

- Backend health endpoint returns `200` and `{"status":"healthy","version":"2.0.0"}`.
- Frontend production build passes with `npm run build`.
- Backend test collection finds 8 tests.
- Normalized exam authoring contract passes against isolated SQLite.
- Grading persistence contract passes against isolated SQLite.
- Legacy extraction-table migration contract passes.
- API router compatibility contract passes with 27 paths.
- Focused diagnostics report no errors in recently changed files.

There are some existing lint warnings in older database/evaluation code, including dynamic SQL and long SQL strings. The newly organized router package is lint-clean.

## Running Locally

Backend:

```bash
cd /Users/pandhari/Desktop/RubricTrace
uv run --project backend uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Backend API docs:

```text
http://127.0.0.1:8000/docs
```

Frontend:

```bash
npm --prefix /Users/pandhari/Desktop/RubricTrace/frontend run dev -- --host 127.0.0.1 --port 5173
```

Frontend URL:

```text
http://127.0.0.1:5173/
```

Do not run `npm run dev` from the repository root because `package.json` lives under `frontend/`.

## Important Current Caveats

- The VLM/LLM provider must be configured and available for real PDF extraction and grading.
- The frontend authoring UI is connected to evaluator configuration endpoints, but evaluator configuration creation UX is still basic.
- The main extraction/grading workflow is implemented, but the frontend still needs deeper production polish around loading states, upload retry behavior, review evidence presentation, and accessibility.
- Full end-to-end PDF/VLM execution has not been run in this session because it requires a configured model provider and real exam files.
- The local `backend/.venv`, frontend `node_modules`, and Vite `dist` are development artifacts and should remain ignored.

## Recommended Next Work

1. Build a production-grade evaluator configuration/settings UI.
2. Add a complete exam authoring flow test using the browser.
3. Add frontend review cards that show extracted page metadata, evidence spans, criterion scores, reasoning, confidence, and threshold used.
4. Add deterministic MCQ/true-false grading before invoking an LLM.
5. Add real PDF fixture tests for page-number extraction and metadata persistence.
6. Add authentication and teacher identity to `teacher_reviews` before production deployment.
7. Add formal database migrations instead of relying only on startup migrations.
8. Add end-to-end Playwright coverage for:
   - create exam
   - add typed questions
   - upload student PDFs
   - process extraction
   - grade
   - review/override
   - inspect final results
```

## Useful Files

- Backend app registration: `backend/app/main.py`
- Exam router: `backend/app/api/routers/exams.py`
- Script router: `backend/app/api/routers/scripts.py`
- Grading router: `backend/app/api/routers/grading.py`
- Review router: `backend/app/api/routers/review.py`
- Database schema/migrations: `backend/app/db/database.py`
- Exam persistence: `backend/app/db/exams.py`
- Pydantic schemas: `backend/app/models/schemas.py`
- Extraction orchestration: `backend/app/extraction/process.py`
- VLM extraction pipeline: `backend/app/extraction/pipeline.py`
- Evaluation pipeline: `backend/app/grading/pipeline.py`
- Frontend API client: `frontend/src/api.js`
- Frontend exam editor: `frontend/src/components/RubricEditor.jsx`
- Frontend exam authoring page: `frontend/src/pages/exam/ExamRubric.jsx`
