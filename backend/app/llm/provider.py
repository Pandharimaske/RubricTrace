"""
Picks which model backend to use for VLM extraction and LLM grading:
local Ollama (default, self-hosted, free), NVIDIA's cloud API (NIM), or
Groq's cloud API (text-only — grading role only, not VLM).

Controlled by RUBRICTRACE_MODEL_PROVIDER=ollama|nvidia|groq (default "ollama").
This is the single place that decides — ai_assistance.py and grader.py just
call get_vlm_client_and_model() / get_llm_client_and_model() and use whatever
they get back; both clients expose the same generate(model, prompt,
image_paths, json_output) signature so nothing else needs to change.

Extraction is single-pass (one structured VLM call per page), so there is no
separate text-model "structuring" role any more: the VLM returns the structured
answers directly.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol, runtime_checkable

from backend.app.llm.ollama import OllamaClient


@runtime_checkable
class ModelClient(Protocol):
    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list | None = None,
        json_output: bool = False,
    ) -> str: ...


# Sensible cloud defaults, overridable per env var. These are NVIDIA API Catalog
# model IDs (provider/model-name) — swap them if a model is deprecated or you'd
# rather use a different one from https://build.nvidia.com.
# The VLM default is Kimi-K3 (strongest free-endpoint vision model on the catalog
# when this was chosen). Fallback if its free endpoint is slow or throttled:
# meta/llama-3.2-90b-vision-instruct. Note that RUBRICTRACE_NVIDIA_VLM_MODEL in
# your .env overrides this default.
_DEFAULT_NVIDIA_VLM_MODEL = "moonshotai/kimi-k3"
_DEFAULT_NVIDIA_LLM_MODEL = "meta/llama-3.1-70b-instruct"

_DEFAULT_OLLAMA_VLM_MODEL = "qwen2.5vl:3b"
_DEFAULT_OLLAMA_LLM_MODEL = "qwen2.5:3b"

# Groq is text-only here — no vision model default, since gpt-oss-120b (and most
# Groq-hosted models) can't read images. Only used for the LLM (grading) role.
_DEFAULT_GROQ_LLM_MODEL = "openai/gpt-oss-120b"


def _provider() -> str:
    return os.getenv("RUBRICTRACE_MODEL_PROVIDER", "ollama").strip().lower()


def _nvidia_vlm_extra_body() -> dict[str, Any] | None:
    """Optional provider-specific request fields for the VLM calls only, from
    RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY (a JSON object). Used for model-specific
    switches such as turning a reasoning model's thinking down or off — check the
    model's page on build.nvidia.com for the exact field. Not applied to grading
    calls, so a field that only one model understands can't break the others."""
    raw = os.getenv("RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("RUBRICTRACE_NVIDIA_VLM_EXTRA_BODY must be a JSON object")
    return parsed


def get_vlm_client_and_model() -> tuple[ModelClient, str]:
    """Client + model name to use for reading exam pages (vision)."""
    if _provider() == "nvidia":
        from backend.app.llm.nvidia import NvidiaClient

        model = os.getenv("RUBRICTRACE_NVIDIA_VLM_MODEL", _DEFAULT_NVIDIA_VLM_MODEL)
        return NvidiaClient(extra_body=_nvidia_vlm_extra_body()), model

    if _provider() == "groq":
        # Groq has no vision default configured — fail loudly rather than silently
        # sending image input to a text-only model. Point RUBRICTRACE_MODEL_PROVIDER
        # at ollama or nvidia for the VLM role instead.
        raise ValueError(
            "RUBRICTRACE_MODEL_PROVIDER=groq has no vision model — Groq's "
            "openai/gpt-oss-120b is text-only. Use RUBRICTRACE_MODEL_PROVIDER="
            "ollama or nvidia for VLM extraction."
        )

    model = os.getenv("RUBRICTRACE_VLM_MODEL", _DEFAULT_OLLAMA_VLM_MODEL)
    return OllamaClient(), model


def get_llm_client_and_model() -> tuple[ModelClient, str]:
    """Client + model name to use for grading (text)."""
    if _provider() == "nvidia":
        from backend.app.llm.nvidia import NvidiaClient

        model = os.getenv("RUBRICTRACE_NVIDIA_LLM_MODEL", _DEFAULT_NVIDIA_LLM_MODEL)
        return NvidiaClient(), model

    if _provider() == "groq":
        from backend.app.llm.groq import GroqClient

        model = os.getenv("RUBRICTRACE_GROQ_LLM_MODEL", _DEFAULT_GROQ_LLM_MODEL)
        return GroqClient(), model

    model = os.getenv("RUBRICTRACE_LLM_MODEL", _DEFAULT_OLLAMA_LLM_MODEL)
    return OllamaClient(), model
