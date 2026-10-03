import base64
import json
import os
from pathlib import Path
from typing import Any

import requests


class ModelUnavailable(RuntimeError):
    """Raised when the configured local Ollama model cannot be used."""


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(os.getenv("RUBRICTRACE_MODEL_TIMEOUT", "300"))

    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list[Path] | None = None,
        json_output: bool = False,
        temperature: float = 0.0,
    ) -> str:
        # temperature=0.0 (greedy decoding) by default: grading and answer
        # extraction should be reproducible -- the same student answer graded
        # twice must produce the same result. Without this, Ollama's default
        # sampling temperature made identical inputs disagree run-to-run by
        # enough to swing MCQ accuracy by ~10 points between two evaluation
        # passes (observed 2026-09-27/28: 96.9% -> 85.7% on the same records).
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if image_paths:
            payload["images"] = [
                base64.b64encode(path.read_bytes()).decode("ascii") for path in image_paths
            ]
        if json_output:
            payload["format"] = "json"

        try:
            response = requests.post(
                f"{self.base_url}/api/generate", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ModelUnavailable(f"Ollama request failed for {model}: {exc}") from exc

        result = response.json().get("response", "").strip()
        if not result:
            raise ModelUnavailable(f"Ollama returned no response for {model}")
        return result


def parse_json_response(response: str) -> dict[str, Any]:
    """Parse JSON from model response, handling various formats (markdown, mixed text, etc)."""
    cleaned = response.strip()

    # Try to extract JSON from markdown code blocks
    if "```" in cleaned:
        # Find content between ``` markers
        parts = cleaned.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # Content between ``` markers
                cleaned = part.strip()
                # Remove language identifier if present (e.g., ```json)
                if cleaned.startswith(("json", "JSON")):
                    cleaned = cleaned[4:].strip()
                break

    # If no code blocks, try to find JSON-like content
    # Look for content between { and } that looks like JSON
    if not cleaned.startswith("{"):
        # Try to extract JSON from mixed text
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            cleaned = cleaned[start_idx : end_idx + 1]

    cleaned = cleaned.strip()

    # Handle empty responses
    if not cleaned:
        raise ModelUnavailable("Model returned empty response")

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Provide more context in error message
        preview = cleaned[:200] if len(cleaned) > 200 else cleaned
        raise ModelUnavailable(f"Model returned invalid JSON. Response preview: {preview}") from exc

    if not isinstance(payload, dict):
        raise ModelUnavailable(
            f"Model JSON response must be an object, got {type(payload).__name__}"
        )

    return payload
