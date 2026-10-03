# RubricTrace

RubricTrace is a self-hosted teacher-in-the-loop framework for rubric-based evaluation of scanned student answer scripts.

The project is designed to be free to run locally: no paid API keys, no closed-model dependency, and no cloud requirement for the MVP.

## MVP Goals

- Upload scanned answer scripts as PDF or image files.
- Convert scripts to page images.
- Extract OCR text and bounding boxes.
- Group extracted text question-wise.
- Score answers against rubric criteria.
- Show evidence, confidence, and review flags.
- Allow teacher score override.
- Compare predicted marks with manual marks from a public dataset.

## Tech Stack

- Backend: FastAPI
- Frontend: React + Vite (industry-grade SPA)
- OCR: PaddleOCR first, with fallback hooks for other OCR engines
- Image/PDF processing: OpenCV, Pillow, PyMuPDF
- Scoring: scikit-learn, sentence-transformers
- Storage: SQLite/PostgreSQL for metadata and local files or S3-compatible object storage

## Project Structure

```text
RubricTrace/
  backend/
    app/
      api/
      core/
      models/
      services/
        confidence/
        evaluation/
        ocr/
        scoring/
    data/
    eval/
    scripts/
    deploy/
    .env.example
  frontend/
    src/
      pages/
      components/
      api.js
      App.jsx
    index.html
    package.json
    vite.config.js
  docs/
  notebooks/
  tests/
```

## Quick Start

```bash
cd ~/Desktop/RubricTrace
cd backend
uv sync
source .venv/bin/activate
```

Run the API:

```bash
./backend/scripts/run_api.sh
```

The API is available at `http://127.0.0.1:8000`. The health check is
`GET /api/health`, and the interactive API documentation is available at
`http://127.0.0.1:8000/docs`.

Run the React frontend:

```bash
./backend/scripts/run_ui.sh
```

Or manually:

```bash
cd frontend
npm install
npm run dev
```

Run both backend and frontend together:

```bash
./backend/scripts/run_all.sh
```

Open the dashboard at `http://localhost:5173`. Start the API before using the
upload or scoring controls in the dashboard.

Teacher evaluation workflow:

1. Upload and process a student script to receive its `script_id`.
2. Submit `POST /api/scripts/{script_id}/evaluate` with golden answers,
  criteria, max marks, and grading guidance for each question.
3. Review saved results with `GET /api/scripts/{script_id}/evaluation`.
4. Approve a corrected score with
  `POST /api/scripts/{script_id}/override` and a teacher reason.

Evaluations are stored in the SQLite database under `backend/data/rubrictrace.db` by default. Each result
contains the extracted answer, criterion evidence, confidence, review status,
optional LLM reasoning, and any teacher override. The React dashboard exposes the
same workflow through its intuitive interface.

## AWS-shaped local development

The development compose file includes PostgreSQL and an S3-compatible object store so the application can be
tested against services that resemble the planned AWS deployment:

```bash
docker compose -f backend/deploy/compose/docker-compose.dev.yml up -d postgres minio
```

To use the local PostgreSQL container instead of SQLite, set:

```env
RUBRICTRACE_DATABASE_URL=postgresql://rubrictrace:rubrictrace@127.0.0.1:15432/rubrictrace
```

The schema is created automatically on startup. The same setting can later point
to an AWS RDS or Aurora PostgreSQL instance. Existing SQLite data is not migrated
automatically; export or migrate it before switching a non-empty environment.

The local object-store service uses RustFS because current MinIO container images
require registry authentication in some environments. RustFS provides the same S3
API at `http://127.0.0.1:9000` with its console at `http://127.0.0.1:9001`. To
mirror uploads to the local object store, set these values in
`backend/.env`:

```env
RUBRICTRACE_STORAGE_BACKEND=s3
RUBRICTRACE_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000
RUBRICTRACE_STORAGE_ACCESS_KEY=minioadmin
RUBRICTRACE_STORAGE_SECRET_KEY=minioadmin
RUBRICTRACE_STORAGE_BUCKET=rubrictrace
RUBRICTRACE_STORAGE_REGION=us-east-1
```

The upload pipeline keeps a local processing copy because the OCR and VLM libraries
consume filesystem paths. The S3 object is the durable copy and is deleted when a
script or exam is deleted. For AWS, use the same S3 client with the endpoint unset,
IAM credentials or a task role, and an S3 bucket name.

Optional extras:

```bash
uv sync --extra ocr --extra dev
# Add `--extra vision` and/or `--extra embeddings` when needed.
```

Use one `uv sync` command containing every extra you want enabled; `uv sync`
reconciles the environment to the requested dependency groups.

The VLM reads scanned pages and question crops. The LLM is the only grader:
it scores each answer against the golden answer and rubric, returns reasoning,
and flags uncertain cases for teacher review. Ollama must be running:

```bash
ollama serve
ollama pull qwen2.5vl:3b
ollama pull qwen2.5:3b
```

Configure models with `RUBRICTRACE_VLM_MODEL` and `RUBRICTRACE_LLM_MODEL`, or
leave those defaults. If Ollama is unavailable, extraction and grading return
an error instead of falling back to keyword matching.

Run the batch pipeline on the bundled demo dataset:

```bash
uv run python backend/scripts/run_pipeline.py backend/data/samples/demo_metadata.csv
```

Import the selected real dataset:

```bash
mkdir -p backend/data/raw/mendeley_sf3kvjwknt
curl -L 'https://data.mendeley.com/public-api/zip/sf3kvjwknt/download/1' \
  -o backend/data/raw/mendeley_sf3kvjwknt/dataset.zip
unzip -q backend/data/raw/mendeley_sf3kvjwknt/dataset.zip \
  -d backend/data/raw/mendeley_sf3kvjwknt/archive
dataset_root="$(find backend/data/raw/mendeley_sf3kvjwknt/archive -mindepth 1 -maxdepth 1 -type d -print -quit)"
uv run python backend/scripts/import_mendeley_dataset.py "$dataset_root" \
  --output backend/data/processed/mendeley_sf3kvjwknt_metadata.csv
uv run python backend/scripts/run_pipeline.py \
  backend/data/processed/mendeley_sf3kvjwknt_metadata.csv \
  --output backend/data/processed/mendeley_sf3kvjwknt_results.json
```

This project uses “A Dataset of Digitized Student Examination Papers, Answer
Keys, and Manual Evaluations for Automated Grading Research”, Mendeley Data,
V1, DOI `10.17632/sf3kvjwknt.1`, licensed CC BY 4.0. It contains 50
anonymized student exams, raw scanned PDFs, an answer key, and item-level
teacher marks for Q1–Q35.

The imported run currently contains 1,750 question records. The baseline
completed OCR on all 200 scanned pages and produced `MAE = 0.878` over 1,567
records with numeric manual marks. Because handwriting and visual MCQ marks are
not consistently recognized by generic Tesseract, 181 records had usable
question-aligned text and 1,569 were correctly flagged `needs_review`. This is
the expected teacher-in-the-loop behavior, not a claim of reliable automatic
grading for every scan.

The command writes `backend/data/processed/pipeline_results.json` with extracted
answers, criterion scores, evidence, confidence, review status, and MAE when
manual marks are present. Dataset metadata can be CSV or JSON. Each record
must provide `student_id`, `script_path`, `question_id`, `max_marks`, and a
`criteria_json` list; `manual_marks` is optional. Script paths are relative to
the metadata file.

## Current Status

Runnable MVP foundation:

- FastAPI health and rubric scoring endpoints are implemented.
- PDF and image uploads are persisted under `backend/data/raw/uploads/`.
- PDF files are converted to page images under `backend/data/processed/page_images/`.
- Modern React SPA provides upload, processing, rubric scoring, and teacher review interface.
- Core dependencies are managed with `uv`; `requirements.txt` is retained as
  as the backend dependency manifest.
- A batch ingestion and scoring pipeline is available through
  `backend/scripts/run_pipeline.py`.

Frontend Features:

- Dashboard with statistics and quick actions
- Student management and detailed student views
- Script upload with drag-and-drop support
- Script processing and question segmentation
- Rubric configuration builder with templates
- Teacher evaluation workflow with AI confidence scoring
- Review queue for low-confidence answers requiring teacher review
- Visual crop images for each question
- Teacher override capabilities with reasoning

See [docs/frontend_guide.md](docs/frontend_guide.md) for detailed frontend documentation.

Still planned:

- Confidence and evaluation service implementations.
- Dataset ingestion, manual-score comparison, and persistent metadata storage.
- Importing and evaluating the selected public examination dataset.

Regression coverage for ingestion, segmentation, and the demo batch pipeline is
available under `tests/`.

The batch pipeline extracts text from text files and text-based PDFs. With the
OCR extra installed, scanned images and image-only PDFs are processed through
local Tesseract OCR. If OCR is unavailable or produces no text, the result is
reported as `needs_ocr` for teacher review.
