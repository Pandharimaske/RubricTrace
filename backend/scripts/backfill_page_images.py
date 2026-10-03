#!/usr/bin/env python3
"""
Render and save page images for scripts already in the DB whose PDFs never
went through the live /scripts/{id}/process pipeline -- e.g. the Mendeley
dataset was inserted directly into the DB by a separate import script, which
skipped the pdf_to_images() render step that normally happens during
processing. This does NOT re-run VLM extraction or grading; it only
backfills the rendered page PNGs under data/processed/page_images/{script_id}/
and fills in scripts.page_count, so the frontend's page slider has images to
show and knows how many pages exist.

Usage:
    python scripts/backfill_page_images.py
    python scripts/backfill_page_images.py --force   # re-render even if a folder already exists
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# backend/ isn't an installed package (pyproject.toml has `package = false`) --
# it only resolves automatically under `uv run` (see scripts/run_api.sh). Add
# the project root here so this also works as a plain `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.settings import PAGE_IMAGE_DIR, ensure_data_dirs
from backend.app.db.database import get_db, update_script_status
from backend.app.services.ocr.pdf import pdf_to_images


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-render even if a page_images folder already exists for the script",
    )
    args = parser.parse_args()

    ensure_data_dirs()

    with get_db() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT script_id, file_path, status, page_count FROM scripts"
            ).fetchall()
        ]

    rendered = 0
    skipped_existing = 0
    skipped_missing_pdf = 0
    failed = 0

    for row in rows:
        script_id = row["script_id"]
        pdf_path = Path(row["file_path"])
        out_dir = PAGE_IMAGE_DIR / script_id

        if out_dir.is_dir() and any(out_dir.iterdir()) and not args.force:
            skipped_existing += 1
            continue

        if not pdf_path.is_file():
            print(f"  SKIP {script_id}: PDF not found at {pdf_path}")
            skipped_missing_pdf += 1
            continue

        try:
            images = pdf_to_images(pdf_path, out_dir)
        except Exception as exc:
            print(f"  FAIL {script_id}: {exc}")
            failed += 1
            continue

        # Preserve whatever status this row already had; only fill in page_count.
        update_script_status(script_id, row["status"] or "processed", page_count=len(images))
        rendered += 1
        print(f"  OK   {script_id}: {len(images)} pages")

    print(
        f"\nDone. rendered={rendered} skipped_existing={skipped_existing} "
        f"skipped_missing_pdf={skipped_missing_pdf} failed={failed}"
    )


if __name__ == "__main__":
    main()
