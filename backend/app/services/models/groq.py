"""
Cloud model client for Groq's OpenAI-compatible API (https://api.groq.com/openai/v1).

Mirrors OllamaClient/NvidiaClient's generate() signature so callers (ai_assistance.py,
grader.py, structured.py) can swap between backends without changing any calling code —
see provider.py, which picks one based on RUBRICTRACE_MODEL_PROVIDER /
RUBRICTRACE_STRUCTURING_PROVIDER.

gpt-oss-120b (the default text model here) is text-only — it has no vision input, so
this client is meant for the LLM/structuring roles, not the VLM extraction role. Point
RUBRICTRACE_STRUCTURING_PROVIDER=groq (or RUBRICTRACE_MODEL_PROVIDER=groq for grading)
at it while keeping VLM extraction on Ollama/NVIDIA.

Beyond the plain json_output flag every client supports, this client also exposes
generate_json_schema(), which uses Groq's native structured-outputs mode (strict JSON
Schema, OpenAI-compatible response_format={"type": "json_schema", ...}). Passing a
Pydantic schema there guarantees schema-conformant JSON back from the API itself,
instead of relying purely on structured.py's post-hoc validate-and-retry loop. That
retry loop is still worth keeping as a fallback for transient failures (rate limits,
timeouts) — it's not made redundant, just less likely to be hit for malformed JSON.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from backend.app.services.models.ollama import ModelUnavailable

if TYPE_CHECKING:
    from pydantic import BaseModel


class GroqClient:
    """Talks to Groq's cloud model catalog via the OpenAI Python SDK."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.base_url = base_url or os.getenv("GROQ_API_BASE_URL", "https://api.groq.com/openai/v1")
        self.timeout = float(os.getenv("RUBRICTRACE_MODEL_TIMEOUT", "120"))
        self._client = None  # lazily constructed so a missing api key raises on use, not import

    def _get_client(self):
        if not self.api_key:
            raise ModelUnavailable(
                "GROQ_API_KEY is not set. Add it to .env (or export it) to use "
                "RUBRICTRACE_MODEL_PROVIDER=groq or RUBRICTRACE_STRUCTURING_PROVIDER=groq."
            )
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency is in pyproject.toml
                raise ModelUnavailable(
                    "The 'openai' package is required for the Groq provider. "
                    "Install it (it's already listed in pyproject.toml)."
                ) from exc
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _build_messages(
        self, prompt: str, image_paths: list[Path] | None, json_output: bool
    ) -> list[dict[str, object]]:
        if image_paths:
            # gpt-oss-120b and most Groq-hosted text models have no vision input.
            # Fail loudly here rather than silently dropping the images and
            # grading/structuring against a prompt the model never actually saw.
            raise ModelUnavailable(
                "GroqClient was called with image_paths, but the configured Groq "
                "model is text-only. Keep VLM extraction on RUBRICTRACE_MODEL_PROVIDER="
                "ollama or nvidia, and only point RUBRICTRACE_STRUCTURING_PROVIDER (or "
                "the grading provider) at groq."
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
        messages.append({"role": "user", "content": prompt})
        return messages

    def _request(
        self,
        model: str,
        messages: list[dict[str, object]],
        response_format: dict[str, object] | None,
    ):
        client = self._get_client()
        # Same reasoning as NvidiaClient: both call sites (structuring, grading) want
        # the model's most faithful reading of the input, not creative variation.
        temperature = float(os.getenv("RUBRICTRACE_MODEL_TEMPERATURE", "0.0"))
        max_tokens = int(os.getenv("RUBRICTRACE_MODEL_MAX_TOKENS", "4096"))
        kwargs: dict[str, object] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # openai raises its own exception hierarchy; normalize it
            raise ModelUnavailable(f"Groq API request failed for {model}: {exc}") from exc

        if not response.choices:
            raise ModelUnavailable(f"Groq API returned no choices for {model}")

        result = (response.choices[0].message.content or "").strip()
        if not result:
            raise ModelUnavailable(f"Groq API returned an empty response for {model}")
        return result

    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list[Path] | None = None,
        json_output: bool = False,
    ) -> str:
        """Plain-text/loose-JSON generation — same contract as OllamaClient/NvidiaClient.
        For schema-guaranteed JSON, prefer generate_json_schema() below instead."""
        messages = self._build_messages(prompt, image_paths, json_output)
        response_format = {"type": "json_object"} if json_output else None
        return self._request(model, messages, response_format)

    def generate_json_schema(
        self,
        model: str,
        prompt: str,
        schema: type[BaseModel],
        schema_name: str | None = None,
    ) -> str:
        """
        Groq's native structured-outputs mode: pass a Pydantic model and get back a
        JSON string guaranteed to conform to its schema (strict mode), instead of
        relying on prompt instructions + structured.py's post-parse validation.

        Not part of the shared ModelClient protocol (it takes a schema, which
        Ollama/NVIDIA clients have no equivalent for) — call it directly from
        structured.py or grader.py when the active client is a GroqClient and you
        want the stronger guarantee. Falls back to nothing on its own; wrap the
        call in the existing generate_structured()/parse_and_validate() path if
        you still want retry-on-validation-error behaviour for edge cases the API
        itself doesn't catch (e.g. semantically wrong but schema-valid output).
        """
        messages = self._build_messages(prompt, image_paths=None, json_output=True)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name or schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },
        }
        return self._request(model, messages, response_format)
