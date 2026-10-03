"""
Cloud model client for NVIDIA's OpenAI-compatible NIM API (https://integrate.api.nvidia.com/v1).

Mirrors OllamaClient's generate() signature so callers (ai_assistance.py, grader.py)
can swap between the local Ollama backend and this cloud backend without changing
any calling code — see provider.py, which picks one based on RUBRICTRACE_MODEL_PROVIDER.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from backend.app.services.models.ollama import ModelUnavailable


class NvidiaClient:
    """Talks to NVIDIA's cloud model catalog via the OpenAI Python SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        # extra_body: provider-specific request fields passed through verbatim
        # (see provider._nvidia_vlm_extra_body). None for most calls.
        self.extra_body = extra_body
        # The OpenAI SDK retries 429/5xx with exponential backoff on its own; the
        # free NIM endpoints rate-limit, so give it more attempts than its default 2.
        self.sdk_max_retries = int(os.getenv("RUBRICTRACE_NVIDIA_SDK_MAX_RETRIES", "5"))
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY")
        self.base_url = base_url or os.getenv(
            "NVIDIA_API_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )
        self.timeout = float(os.getenv("RUBRICTRACE_MODEL_TIMEOUT", "120"))
        self._client = None  # lazily constructed so a missing api key raises on use, not import

    def _get_client(self):
        if not self.api_key:
            raise ModelUnavailable(
                "NVIDIA_API_KEY is not set. Add it to .env (or export it) to use "
                "RUBRICTRACE_MODEL_PROVIDER=nvidia."
            )
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency is in pyproject.toml
                raise ModelUnavailable(
                    "The 'openai' package is required for the NVIDIA provider. "
                    "Install it (it's already listed in pyproject.toml)."
                ) from exc
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=self.sdk_max_retries,
            )
        return self._client

    @staticmethod
    def _image_to_data_url(path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list[Path] | None = None,
        json_output: bool = False,
    ) -> str:
        client = self._get_client()

        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths or []:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_to_data_url(image_path)},
                }
            )

        messages: list[dict[str, object]] = []
        if json_output:
            messages.append(
                {
                    "role": "system",
                    "content": "Respond with ONLY valid JSON. No markdown, no code "
                    "fences, no commentary before or after the JSON.",
                }
            )
        messages.append({"role": "user", "content": content})

        # Not every model on the NVIDIA catalog honors response_format, so we don't
        # force it — the prompts already instruct "JSON only, no markdown" and
        # parse_json_response() strips ``` fences defensively if a model adds them.
        # Both call sites here (page transcription, JSON structuring/grading) want the
        # model's most faithful reading of what's in front of it, not creative
        # variation, so temperature defaults to 0 across the board.
        temperature = float(os.getenv("RUBRICTRACE_MODEL_TEMPERATURE", "0.0"))
        # A page extraction returns the raw transcript AND the per-question answers
        # in one JSON object (the text appears twice), and a reasoning model's
        # thinking tokens can count against this budget too, so it needs real headroom.
        max_tokens = int(os.getenv("RUBRICTRACE_MODEL_MAX_TOKENS", "8192"))
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if self.extra_body:
            request["extra_body"] = self.extra_body
        try:
            response = client.chat.completions.create(**request)
        except Exception as exc:  # openai raises its own exception hierarchy; normalize it
            raise ModelUnavailable(f"NVIDIA API request failed for {model}: {exc}") from exc

        if not response.choices:
            raise ModelUnavailable(f"NVIDIA API returned no choices for {model}")

        result = (response.choices[0].message.content or "").strip()
        if not result:
            raise ModelUnavailable(f"NVIDIA API returned an empty response for {model}")
        return result
