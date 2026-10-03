# data/

Local datasets and generated artifacts live here. Nothing in this directory is tracked by git except this file (see `.gitignore`).

- `data/raw/` holds downloaded source datasets.
- `data/processed/` holds derived files (splits, extracted transcripts).

Current local assets:

- `raw/exam_rubric.json`: Data Science examination questions, answer key, and rubrics.
- `raw/mendeley_sf3kvjwknt/`: source Mendeley dataset, including student PDFs and manual marks.
- `raw/extracted_answers/`: Claude-extracted question answers for students 1-50,
	used as source input by the grading scripts.
- `processed/extracted_answers_index.json`: generated flat index consumed by the
	grading scripts.

Instructions for obtaining the evaluation dataset are added to `docs/eval/` once the eval pipeline lands.
