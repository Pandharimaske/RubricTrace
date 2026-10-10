"""Parsing of raw model text into JSON objects."""

from __future__ import annotations

import json
from typing import Any

from backend.app.llm.errors import ModelUnavailable


def parse_json_response(response: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating markdown fences and prose
    around the object."""
    cleaned = response.strip()

    if "```" in cleaned:
        parts = cleaned.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # content between ``` markers
                cleaned = part.strip()
                if cleaned.startswith(("json", "JSON")):
                    cleaned = cleaned[4:].strip()
                break

    if not cleaned.startswith("{"):
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            cleaned = cleaned[start_idx : end_idx + 1]

    cleaned = cleaned.strip()
    if not cleaned:
        raise ModelUnavailable("Model returned empty response")

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ModelUnavailable(
            f"Model returned invalid JSON. Response preview: {cleaned[:200]}"
        ) from exc

    if not isinstance(payload, dict):
        raise ModelUnavailable(
            f"Model JSON response must be an object, got {type(payload).__name__}"
        )
    return payload
