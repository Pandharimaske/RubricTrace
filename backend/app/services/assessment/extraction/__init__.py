"""Page extraction: render scripts, read each page with a VLM, and merge results.

- pdf.py, blank_page.py     Page rendering and blank-page checks.
- prompt.py                 The extraction prompt.
- pipeline.py               One structured VLM call per page.
- merge.py, review_flags.py Pure merge and review logic.
- metrics.py, cache.py      Per-run counters and on-disk cached page results.
- segment.py, questions.py  Script splitting and the script-level entry point.
- process.py                Stored-script extraction and persistence.

Everything except pipeline.py is plain functions over dicts/strings and is
unit-testable without mocking an LLM.
"""

from __future__ import annotations
