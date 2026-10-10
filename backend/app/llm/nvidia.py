"""
Client for NVIDIA's OpenAI-compatible NIM API (https://integrate.api.nvidia.com/v1), the
only model backend RubricTrace uses. See provider.py for how the VLM and LLM are chosen.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from backend.app.core.config import settings
from backend.app.llm.errors import ModelUnavailable


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
        self.sdk_max_retries = settings.nvidia_sdk_max_retries
        self.api_key = api_key or settings.nvidia_api_key
        self.base_url = base_url or settings.nvidia_api_base_url
        self.timeout = settings.model_timeout
        # Built lazily so a missing api key raises on first use, not at import.
        self._client: Any = None

    def _get_client(self):
        if not self.api_key:
            raise ModelUnavailable(
                "NVIDIA_API_KEY is not set. Add it to backend/.env (or export it); get a key at "
                "https://build.nvidia.com/settings."
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
        temperature = settings.model_temperature
        # A page extraction returns the raw transcript AND the per-question answers
        # in one JSON object (the text appears twice), and a reasoning model's
        # thinking tokens can count against this budget too, so it needs real headroom.
        max_tokens = settings.model_max_tokens
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
