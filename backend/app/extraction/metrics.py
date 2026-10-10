"""Per-run counters for the extraction pipeline, split into two categories:

- PROMPT-QUALITY signals: cases where the pipeline's own retry/self-check logic
  had to intervene because the model didn't follow instructions on the first
  try (a completeness-nudge retry firing, or the same-page duplicate-value
  fabrication catch firing). These are expected to trend toward zero as the
  extraction prompt improves; a nonzero, non-shrinking rate here across runs is
  a signal the prompt itself still needs work, not a reason to remove the safety
  net that's catching it.

- STRUCTURAL signals: cases with no possible prompt fix, because the
  information genuinely isn't recoverable from the page image alone (a page
  with no visible question numbering at all, a genuinely ambiguous handwritten
  digit that collides with another question, or an answer the model reports as
  illegible). These are expected to persist regardless of model quality and
  represent real teacher-review workload, not a bug to eliminate by prompting
  harder.

Logged as one summary per script processed, so the trend is visible across a
batch run without digging through per-page debug output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class ExtractionMetrics:
    pages_total: int = 0
    pages_blank_skipped: int = 0
    pages_no_content: int = 0
    pages_cached: int = 0

    # Prompt-quality signals: expect these to trend toward zero as the
    # extraction prompt improves.
    completeness_retries_triggered: int = 0
    completeness_retries_recovered: int = 0
    suspect_duplicate_groups: int = 0
    suspect_duplicate_items: int = 0
    never_mentioned_items: int = 0

    # Structural signals: expected to persist regardless of prompt quality.
    unassigned_items: int = 0
    digit_conflict_items: int = 0
    illegible_items: int = 0

    def log_summary(self) -> None:
        log.info(
            "metrics pages: %d total, %d blank-skipped, %d no-content, %d from cache",
            self.pages_total,
            self.pages_blank_skipped,
            self.pages_no_content,
            self.pages_cached,
        )
        log.info(
            "metrics prompt-quality signals (expect -> 0 as prompts improve): completeness "
            "retries %d (%d recovered), suspect-duplicate groups %d (%d items), "
            "never-mentioned IDs %d",
            self.completeness_retries_triggered,
            self.completeness_retries_recovered,
            self.suspect_duplicate_groups,
            self.suspect_duplicate_items,
            self.never_mentioned_items,
        )
        log.info(
            "metrics structural signals (persist regardless of prompt quality): unassigned "
            "items %d, digit-conflict items %d, illegible items %d",
            self.unassigned_items,
            self.digit_conflict_items,
            self.illegible_items,
        )
