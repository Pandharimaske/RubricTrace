"""
Picks the model client + model name for each role: the VLM that reads exam pages and the LLM
that grades answers. Both run on NVIDIA's cloud API (NIM); there is no local-model backend.

Callers ask a ``ModelProvider`` for ``vlm()`` or ``llm()`` and get back a client exposing
``generate(model, prompt, image_paths, json_output)`` plus the model name to pass it. Tests
and scripts inject their own client with the same shape, so nothing here needs mocking.

Extraction is single-pass (one structured VLM call per page), so there is no separate
text-model "structuring" role: the VLM returns the structured answers directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.app.core.config import Settings, settings
from backend.app.llm.nvidia import NvidiaClient


@runtime_checkable
class ModelClient(Protocol):
    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list[Path] | None = None,
        json_output: bool = False,
    ) -> str: ...


class ModelProvider:
    """Resolves the (client, model name) pair for each role from settings.

    Model IDs are NVIDIA API Catalog names (provider/model-name); swap them with
    RUBRICTRACE_NVIDIA_VLM_MODEL / RUBRICTRACE_NVIDIA_LLM_MODEL if one is deprecated.
    The key comes from NVIDIA_API_KEY.
    """

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or settings

    def _vlm_extra_body(self) -> dict[str, Any] | None:
        """Optional provider-specific request fields for VLM calls only, from
        RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY (a JSON object) -- e.g. to turn a reasoning model's
        thinking down. Not applied to grading calls, so a field only one model understands
        cannot break the others."""
        raw = self._config.nvidia_vlm_extra_body.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY must be a JSON object")
        return parsed

    def vlm(self) -> tuple[ModelClient, str]:
        """Client + model for reading exam pages (vision)."""
        return NvidiaClient(extra_body=self._vlm_extra_body()), self._config.nvidia_vlm_model

    def llm(self) -> tuple[ModelClient, str]:
        """Client + model for grading (text)."""
        return NvidiaClient(), self._config.nvidia_llm_model
