"""On-disk cache of per-page extraction results.

A page costs one (slow, rate-limited) VLM call. Re-running a script after a crash, a
rate-limit failure, or a downstream code change should not re-spend that quota, so each
page's parsed PageExtraction is stored under a hash of everything that could change the
result: the page image bytes, the model, the prompt version, the question list/types, and
the previous-question hint.

Env:
  RUBRICTRACE_EXTRACTION_CACHE       set to 0/false/off to disable (default: on)
  RUBRICTRACE_EXTRACTION_CACHE_DIR   override the directory
                                     (default: data/processed/extraction_cache)
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.llm.structured import PageExtraction

log = logging.getLogger(__name__)


class ExtractionCache:
    def __init__(self, config: Settings, enabled: bool | None = None) -> None:
        self._config = config
        self._enabled = config.extraction_cache if enabled is None else enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def directory(self) -> Path:
        # Read lazily so a changed setting (tests, env override) is honoured.
        return self._config.resolved_extraction_cache_dir

    @staticmethod
    def key(
        page_path: Path,
        model: str,
        prompt_version: str,
        question_ids: list[str] | None,
        question_types: dict[str, str] | None,
        previous_question_id: str | None,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(page_path.read_bytes())
        meta = {
            "model": model,
            "prompt_version": prompt_version,
            "question_ids": question_ids or [],
            "question_types": question_types or {},
            "previous_question_id": previous_question_id,
        }
        digest.update(json.dumps(meta, sort_keys=True).encode("utf-8"))
        return digest.hexdigest()

    def load(self, key: str) -> PageExtraction | None:
        if not self._enabled:
            return None
        try:
            return PageExtraction.model_validate_json(
                (self.directory / f"{key}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def store(self, key: str, page: PageExtraction) -> None:
        if not self._enabled:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.directory / f"{key}.json.tmp"
            tmp.write_text(page.model_dump_json(), encoding="utf-8")
            tmp.replace(self.directory / f"{key}.json")
        except OSError as exc:  # a cache failure must never fail extraction
            log.warning("cache write failed (%s); continuing without caching", exc)
