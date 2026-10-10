"""Page extraction: render scripts, read each page with a VLM, and merge results.

- ScriptReader              Script file -> per-question answers (renders pages, runs the extractor).
- ScriptExtractor           One structured VLM call per page, merged into one answer set.
- ScriptProcessor           Reads a stored script and persists its extractions.
- pdf.py, blank_page.py     Page rendering and blank-page checks.
- prompt.py                 The extraction prompt.
- merge.py, review_flags.py Pure merge and review logic.
- metrics.py, cache.py      Per-run counters and on-disk cached page results.
- segment.py                Splitting plain-text scripts on question headings.
"""

from backend.app.extraction.pipeline import ExtractionOutcome, ScriptExtractor
from backend.app.extraction.process import ProcessedScript, ScriptProcessor
from backend.app.extraction.reader import ScriptReader, ScriptReading

__all__ = [
    "ExtractionOutcome",
    "ProcessedScript",
    "ScriptExtractor",
    "ScriptProcessor",
    "ScriptReader",
    "ScriptReading",
]
