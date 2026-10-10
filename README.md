# RubricTrace

Teacher-in-the-loop grading for handwritten exam scripts. A teacher sets up an exam once
(questions, question types, reference answers, optional rubric, max marks), uploads scanned
student papers in bulk, and RubricTrace reads every answer with a vision-language model, grades
it against the reference answer, explains each mark, and sends the answers it is unsure about
to a review queue. Teacher decisions are final and are never overwritten by a re-grade.

Self-hosted application with a cloud model backend: SQLite by default, models served by the
NVIDIA API, and PostgreSQL / S3-compatible storage optional. No local model runtime is needed.

## How it works

1. **Author the exam**: add questions of any type (MCQ, true/false, short answer, long answer),
   the answer key, options, rubric criteria, and a review-confidence threshold.
2. **Upload scripts** (PDF or images) in bulk. Files are validated by type and size.
3. **Read**: each page is rendered to an image and sent to a VLM once, which returns the
   page's answers attached to question numbers. Blank pages are skipped, continuations across
   pages are merged, and illegible, duplicated or unattributable content is flagged for review.
   Page results are cached on disk so a crash or re-run does not re-spend model quota.
4. **Grade**: objective questions whose marked option is unambiguous are graded
   deterministically (no model call, full confidence). Everything else is graded by an LLM
   against the reference answer and rubric, with reasoning and per-criterion scores.
5. **Review**: an answer whose confidence is below its threshold lands in the review queue. The
   threshold is configuration, so changing it re-sorts existing grades without a re-grade.
6. **Results**: a student x question grid with totals and CSV export.

## Architecture

```text
RubricTrace/
  backend/
    app/
      main.py            create_app(): wires the container, CORS, routers, built frontend
      container.py       Container: builds every service from one Settings + Database
      api/               FastAPI routers (exams, scripts, grading, review, ...) + deps.py
      core/              Settings (pydantic-settings), UploadStorage (+ S3 / Supabase mirrors)
      db/                Database (connection + schema + migrations), repository classes
      models/            domain.py (QuestionSpec, GradeResult, ReviewPolicy), API schemas
      extraction/        ScriptReader, ScriptExtractor, ScriptProcessor, merge/review logic
      grading/           evaluators, AnswerGrader, GradingService, BatchGrader, JobRunner
      llm/               ModelProvider and the NVIDIA client, structured-output validation
    scripts/             batch evaluation and maintenance scripts (see scripts/README.md)
    tests/               unit/ and integration/ (in-process API flow, fake models)
    deploy/              docker compose for Postgres + S3-compatible storage
  frontend/              React + Vite + React Router + TanStack Query
  docs/
```

The backend is class-based with explicit dependencies: repositories take a `Database`,
services take repositories and evaluators, and the `Container` is the only place they are
assembled. Tests build their own container with a temporary database and fake models.

## Quick start

```bash
cd backend
uv sync
cp .env.example .env        # then set NVIDIA_API_KEY
cd ..
./backend/scripts/run_api.sh    # API on http://127.0.0.1:8000 (docs at /docs)
./backend/scripts/run_ui.sh     # UI on http://localhost:5173
```

`./backend/scripts/run_all.sh` starts both. Check the environment with
`uv run python backend/scripts/check_setup.py`.

### Models

Both models run on NVIDIA's API (get a key at https://build.nvidia.com/settings and set
`NVIDIA_API_KEY` in `backend/.env`):

- **Vision (reads the pages):** `moonshotai/kimi-k3`, override with
  `RUBRICTRACE_NVIDIA_VLM_MODEL`. If its free endpoint is slow or throttled, try
  `meta/llama-3.2-90b-vision-instruct`.
- **Grading:** `openai/gpt-oss-20b`, override with `RUBRICTRACE_NVIDIA_LLM_MODEL`.

If the API is unreachable or the key is missing, extraction and grading fail with a clear
error instead of falling back to keyword matching.

### Other configuration

All settings are `RUBRICTRACE_*` environment variables (see `backend/.env.example`):
`CORS_ORIGINS` (JSON list), `MAX_UPLOAD_MB`, `DATA_ROOT`, `DATABASE_URL` (PostgreSQL),
`STORAGE_BACKEND` (`local` | `s3` | `supabase`), `EXTRACTION_CACHE`, `MODEL_TIMEOUT`.

### PostgreSQL and S3-compatible storage (optional)

```bash
docker compose -f backend/deploy/compose/docker-compose.dev.yml up -d postgres minio
```

```env
RUBRICTRACE_DATABASE_URL=postgresql://rubrictrace:rubrictrace@127.0.0.1:15432/rubrictrace
RUBRICTRACE_STORAGE_BACKEND=s3
RUBRICTRACE_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000
RUBRICTRACE_STORAGE_ACCESS_KEY=minioadmin
RUBRICTRACE_STORAGE_SECRET_KEY=minioadmin
RUBRICTRACE_STORAGE_BUCKET=rubrictrace
```

Uploads always keep a local processing copy (the PDF/VLM libraries read file paths); the S3
object is the durable copy and is removed when a script or exam is deleted. SQLite data is not
migrated automatically when switching to PostgreSQL.

## Evaluation

The grader is benchmarked on the public Mendeley dataset "A Dataset of Digitized Student
Examination Papers, Answer Keys, and Manual Evaluations for Automated Grading Research"
(DOI `10.17632/sf3kvjwknt.1`, CC BY 4.0): 50 anonymised students, Q1-Q35, with item-level
teacher marks.

On the multiple-choice track (1,000 records) the LLM grader reaches QWK 0.94 and 96.9%
exact-match accuracy against the teacher's marks. Short-answer grading (567 attempted
answers) is weaker and is being improved on a held-out test split before numbers are
reported.

Scripts for reproducing this live in `backend/scripts/` (`run_grading_parallel.sh`,
`merge_grading_results.py`, `compute_grading_metrics.py`). The metrics script needs
`uv sync --group eval`. Run the batch pipeline on the bundled demo data:

```bash
uv run --project backend python backend/scripts/run_pipeline.py backend/data/samples/demo_metadata.csv
```

## Development

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

Tests use fake model clients and a temporary data directory, so they need no models, network or
running server. `tests/integration/test_api_integration.py` additionally runs against a live
server on `localhost:8000` when one is up and skips otherwise.
