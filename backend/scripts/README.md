# Backend Scripts

These scripts are kept in one executable directory because the shell launchers and
README commands invoke them by stable paths.

## Development and services

- `run_api.sh`: start the FastAPI backend.
- `run_ui.sh`: start the Vite frontend.
- `run_all.sh`: start backend and frontend together.
- `check_setup.py`: check the installed packages, that `RUBRICTRACE_DATABASE_URL` is set and
  reachable, and that the schema has been applied. Exits non-zero if anything is missing.

## Data preparation

- `build_mendeley_manifest.py`: convert the source Mendeley files into grading metadata.
- `import_mendeley_to_db.py`: load the graded Mendeley benchmark into the application database
  as a normal exam (answer key with structured options and criteria, all scripts, answers with
  their pages, grades). Validates the key against the app's rules, imports one transaction per
  student, and is idempotent. Takes `--merged`, `--index`, `--exam-name` and
  `--review-threshold`.
- `build_extracted_answers_index.py`: flatten `data/raw/extracted_answers/` into
  `data/processed/extracted_answers_index.json`.
- `run_pipeline.py`: run the batch evaluation pipeline from metadata.

## Grading and evaluation

These write JSON under `data/processed/` and do not touch the database.

- `grade_mcq.py`: grade objective answers with an NVIDIA-hosted model.
- `grade_short_answer_cloud.py`: grade short answers with an NVIDIA-hosted model.
- `run_grading_parallel.sh`: run both grading tracks and merge their output.
- `merge_grading_results.py`: combine grading tracks into `results_merged.json`.
- `compute_grading_metrics.py`: calculate evaluation metrics.
- `analyze_short_answer_by_question.py`: analyze short-answer performance by question.
- `inspect_mcq_review_flags.py`: inspect objective-answer review flags.
- `inspect_question.py`: inspect one question's grading records.

## Diagnostics

- `diagnose_extraction.py`: run the vision extraction on one script and print what it read.
  Useful before a full batch.

Page images no longer need a backfill: they are rendered the first time a page is viewed, and
the importer records page numbers itself.

All Python scripts can be run from the repository root with:

```bash
uv run --project backend python backend/scripts/<script>.py
```
