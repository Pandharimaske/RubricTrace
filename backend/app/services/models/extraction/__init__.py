"""Extraction pipeline internals.

- merge.py         Pure merge logic (no model calls) for combining one page's
                    {question_id: answer} result into the script-wide mapping,
                    including continuation handling and misread-digit conflict
                    detection.
- review_flags.py  Pure validation/flagging logic (no model calls): the
                    transcript-completeness cross-check, same-page duplicate-
                    value fabrication detection, and unassigned-content
                    handling with sequential-ID suggestions.
- metrics.py        Per-run counters, split into prompt-quality signals
                    (expected to shrink as prompts improve) and structural
                    signals (expected to persist regardless of prompt
                    quality, since some information genuinely isn't
                    recoverable from a page image alone).
- cache.py          On-disk cache of per-page extraction results, so a rerun
                    or a crash doesn't re-spend VLM quota on pages already read.
- pipeline.py       Orchestration: one structured VLM call per page (the page
                    image in, transcript + per-question answers out), then the
                    above wired together. The only module here that talks to a
                    model client.

Everything except pipeline.py is plain functions over dicts/strings and is
unit-testable without mocking an LLM.
"""

from __future__ import annotations
