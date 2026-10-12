# RubricTrace

Teacher-in-the-loop grading for handwritten exam scripts. A teacher sets up an exam once
(questions, question types, reference answers, optional rubric, max marks), uploads scanned
student papers in bulk, and RubricTrace reads every answer with a vision-language model, grades
it against the reference answer, explains each mark, and sends the answers it is unsure about
to a review queue. Teacher decisions are final and are never overwritten by a re-grade, and the
AI's own mark is always kept next to the teacher's, so agreement between the two can be measured.

Self-hosted application with cloud backends: PostgreSQL (Supabase) for data and the NVIDIA API
for models. Uploads and page images live on local disk, with an optional backup copy in object
storage. No local model runtime is needed.

## How it works

1. **Author the exam**: add questions of any type (MCQ, true/false, short answer, long answer),
   the answer key, options, rubric criteria, a review-confidence threshold and a marking step.
2. **Upload scripts** (PDF or images) in bulk, inside an exam. Files are validated by type and
   size; a student has one script per exam and a byte-identical file is rejected.
3. **Read**: each page is rendered to an image and sent to a VLM once, which returns the
   page's answers attached to question numbers. Blank pages are skipped, continuations across
   pages are merged, and illegible, duplicated or unattributable content is flagged for review.
   Page results are cached on disk so a crash or re-run does not re-spend model quota. A
   question the model never found is recorded as `not_found`, distinct from one found blank.
4. **Grade**: objective questions whose marked option is unambiguous are graded
   deterministically (no model call, full confidence). Everything else is graded by an LLM
   against the reference answer and rubric, with reasoning and per-criterion scores. Written
   answers are rounded to the exam's marking step (whole marks by default). A blank answer scores
   0 without a model call.
5. **Review**: an answer lands in the review queue when its confidence is below its threshold,
   or when a written answer earned 0 marks (the model's costliest miss, flagged however
   confident it was). The threshold is configuration, so changing it re-sorts existing grades
   without a re-grade.
6. **Results**: a student x question grid with totals and CSV export. A mark a teacher changed
   shows the AI's original on hover.

## Architecture

```text
RubricTrace/
  supabase/
    migrations/          the database schema (PostgreSQL), see docs/database.md
  backend/
    app/
      main.py            create_app(): wires the container, CORS, routers, built frontend
      container.py       Container: builds every service from one Settings + Database
      api/               FastAPI routers (exams, scripts, grading, review, ...) + deps.py
      core/              Settings (pydantic-settings), UploadStorage (+ S3 / Supabase mirrors)
      db/                Database (connection pool, schema check), repository classes
      models/            domain.py (QuestionSpec, GradeResult, ReviewPolicy), API schemas
      extraction/        ScriptReader, ScriptExtractor, ScriptProcessor, merge/review logic
      grading/           evaluators, AnswerGrader, GradingService, BatchGrader, JobRunner
      llm/               ModelProvider and the NVIDIA client, structured-output validation
    scripts/             batch evaluation and maintenance scripts (see scripts/README.md)
    tests/               unit/ and integration/ (in-process API flow, fake models, real Postgres)
  frontend/              React + Vite + React Router + TanStack Query
  docs/                  database.md, the architecture figure and the research paper
```

The backend is class-based with explicit dependencies: repositories take a `Database`,
services take repositories and evaluators, and the `Container` is the only place they are
assembled. Tests build their own container with a throwaway database schema and fake models.

### Data model, in short

Full detail is in [docs/database.md](docs/database.md). The decisions that matter:

- **The AI's mark is immutable.** `ai_marks` is what the model awarded; `final_marks` is what
  counts and equals it until a teacher overrides it. A re-grade replaces AI grades but never a
  teacher-approved one, enforced in SQL.
- **"Needs review" is derived, never stored.** A view computes it from confidence, the threshold
  currently configured for the question or exam, and any rule-based flags. The same goes for a
  script's graded / needs-review status, so nothing can go stale.
- **One answer key.** Questions keep their row ids across edits (upsert by question id), so
  grades reference them with a real foreign key and each grade keeps a snapshot of exactly what
  was graded.
- **Invariants live in the database:** marks within bounds, one script per student per exam, at
  most one active job per exam, and row-level security on every table with no policies, so the
  Supabase anon key reads nothing.

## Quick start

```bash
cd backend
uv sync
cp .env.example .env        # set NVIDIA_API_KEY and RUBRICTRACE_DATABASE_URL (next section)
cd ..
uv run --project backend python backend/scripts/check_setup.py   # packages, database, schema
./backend/scripts/run_api.sh    # API on http://127.0.0.1:8000 (docs at /docs)
./backend/scripts/run_ui.sh     # UI on http://localhost:5173
```

`./backend/scripts/run_all.sh` starts both.

### Database (Supabase)

1. Create a Supabase project. Pick the region nearest you: every query is a network round trip.
2. Apply the schema: paste `supabase/migrations/20261010000000_init.sql` into the SQL editor,
   or use the CLI (`supabase link`, then `supabase db push`).
3. Press **Connect** in the dashboard and copy the **Transaction pooler** URI. Put it in
   `backend/.env` with your database password (URL-encode special characters):

   ```env
   RUBRICTRACE_DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

4. Run `check_setup.py`. It reports whether the database is reachable and the schema present.

The database is required; there is no local fallback. The service-role key is a backend secret,
so keep it out of the frontend.

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
`CORS_ORIGINS` (JSON list), `MAX_UPLOAD_MB`, `DATA_ROOT`, `DATABASE_URL`, `DB_POOL_MAX`,
`STORAGE_BACKEND` (`local` | `s3` | `supabase`), `EXTRACTION_CACHE`, `MODEL_TIMEOUT`.

### File storage

Uploads are saved to `backend/data/raw/uploads/` and page images are rendered to
`backend/data/processed/page_images/`, so the app needs the machine that holds `backend/data`.
Setting `RUBRICTRACE_STORAGE_BACKEND` to `supabase` or `s3` also writes a backup copy of each
original to object storage:

- **Supabase:** create a private bucket first (the app does not create it), then set
  `RUBRICTRACE_STORAGE_BUCKET`, `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.
- **S3-compatible:** set the `RUBRICTRACE_STORAGE_*` endpoint, keys and bucket.

The copy is write-only: nothing is restored from it, page images are not copied, and a failed
copy is logged rather than raised. Making object storage the source of truth is not done yet
(see below).

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

### Browsing the benchmark in the app

`import_mendeley_to_db.py` loads the graded benchmark (`results_merged.json`) into the database
as a normal exam: the answer key with structured MCQ options and rubric criteria, all 50
students' scripts, each extracted answer with its page, and every grade. It validates the key
against the app's own rules before saving and stops with a list of problems if anything does not
conform.

```bash
uv run --project backend python backend/scripts/import_mendeley_to_db.py \
  --merged path/to/results_merged.json
```

It is idempotent and writes one transaction per student, so it takes about a minute and a half
against a remote database. Marks a teacher approved in the app are never overwritten by a
re-import. If the results file does not record the review threshold of your grading run, pass
`--review-threshold`.

## Development

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

Unit tests need nothing. The integration tests run the API in process with fake model clients
against a real PostgreSQL: each test creates its own schema, applies the migrations to it, and
drops it afterwards. Point `RUBRICTRACE_TEST_DATABASE_URL` at a throwaway database; without it
those tests are skipped:

```bash
docker run -d --name rt-test-pg -p 55432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
export RUBRICTRACE_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres
```

CI runs the same suite against a Postgres service container. `tests/integration/test_api_integration.py`
additionally runs against a live server on `localhost:8000` when one is up and skips otherwise.

To change the schema, add a new file under `supabase/migrations/` with a later timestamp rather
than editing an applied one (see [docs/database.md](docs/database.md)).

## Known limitations

- **No authentication or per-teacher ownership.** The app is single-tenant. Row-level security
  is on with no policies, so only the backend's own database connection can read data.
- **Files live on local disk.** The object-storage copy is a backup, not the source of truth,
  and the `script_pages` table in the schema is not used yet.
- **Rubric templates were removed.** Every exam has its own answer key; a "duplicate exam"
  action is not built yet.
- Short-answer accuracy is still being improved (see Evaluation).
