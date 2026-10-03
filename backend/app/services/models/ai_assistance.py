"""
Backwards-compatible façade for the VLM extraction pipeline.

The real implementation lives in:
  - backend.app.services.models.prompts.extraction
      The single-pass extraction prompt (stable system prompt + per-call
      user-prompt builder). See prompts/__init__.py for the template shape.
  - backend.app.services.models.extraction.merge
      Pure merge logic (no model calls) for combining one page's result into
      the script-wide question_id -> answer mapping, including continuation
      handling and misread-digit conflict detection.
  - backend.app.services.models.extraction.review_flags
      Pure validation/flagging logic (no model calls): the transcript-
      completeness cross-check, same-page duplicate-value fabrication
      detection, and unassigned-content handling with sequential-ID
      suggestions.
  - backend.app.services.models.extraction.metrics
      Per-run counters, split into prompt-quality signals (expected to
      shrink as prompts improve) and structural signals (expected to persist
      regardless of prompt quality).
  - backend.app.services.models.extraction.cache
      On-disk per-page result cache.
  - backend.app.services.models.extraction.pipeline
      Orchestration: one structured VLM call per page, wiring all of the
      above together. The only module that talks to a model client.

This module exists purely so nothing outside backend/app/services/models/
needs to change import paths.
"""

from __future__ import annotations

from backend.app.services.models.extraction.pipeline import (
    vlm_extract_questions,
    vlm_transcribe_extractions,
)

__all__ = [
    "vlm_extract_questions",
    "vlm_transcribe_extractions",
]
