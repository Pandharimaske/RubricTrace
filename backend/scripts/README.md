# Backend Scripts

These scripts are kept in one executable directory because the shell launchers and
README commands invoke them by stable paths.

## Development and services

- `run_api.sh`: start the FastAPI backend.
- `run_ui.sh`: start the Vite frontend.
- `run_all.sh`: start backend and frontend together.
- `run_models.sh`: start local model services.
- `check_setup.py`: check local development prerequisites.

## Data preparation

- `build_mendeley_manifest.py`: convert the source Mendeley files into grading metadata.
- `import_mendeley_to_db.py`: import the Mendeley exam and scripts into the application database.
- `build_extracted_answers_index.py`: flatten `data/raw/extracted_answers/` into
  `data/processed/extracted_answers_index.json`.
- `run_pipeline.py`: run the batch evaluation pipeline from metadata.

## Grading and evaluation

- `grade_mcq_local.py`: grade objective answers with the local model.
- `grade_short_answer_cloud.py`: grade short answers with a configured cloud model.
- `run_grading_parallel.sh`: run both grading tracks and merge their output.
- `merge_grading_results.py`: combine grading tracks.
- `compute_grading_metrics.py`: calculate evaluation metrics.
- `analyze_short_answer_by_question.py`: analyze short-answer performance by question.
- `inspect_mcq_review_flags.py`: inspect objective-answer review flags.
- `inspect_question.py`: inspect one question's grading records.

## Maintenance and diagnostics

- `backfill_page_images.py`: render missing page images for stored scripts.
- `backfill_page_numbers.py`: backfill page numbers from extraction output.
- `diagnose_extraction.py`: diagnose extraction for one stored script.

All Python scripts can be run from the repository root with:

```bash
uv run --project backend python backend/scripts/<script>.py
```
