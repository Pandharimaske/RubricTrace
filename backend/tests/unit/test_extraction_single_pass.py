"""Unit tests for single-pass page extraction (extraction/pipeline.py).

The model client is a scripted fake: each generate() call pops the next canned JSON
response, so the tests check wiring (one call per page, continuation handling,
caching, illegible flagging, the completeness retry) without any real model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from backend.app.core.config import Settings, settings
from backend.app.extraction.pipeline import ExtractionOutcome, ScriptExtractor
from backend.app.llm.structured import PageExtraction
from PIL import Image, ImageDraw

QUESTION_IDS = ["Q1", "Q2", "Q3"]


class FakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(
        self,
        model: str,
        prompt: str,
        image_paths: list | None = None,
        json_output: bool = False,
    ) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.responses.pop(0))


def _page(path: Path, seed: int) -> Path:
    """A page with enough ink to not count as blank; `seed` makes bytes differ."""
    image = Image.new("RGB", (400, 400), "white")
    ImageDraw.Draw(image).rectangle([20, 20 + seed * 40, 380, 120 + seed * 40], fill="black")
    image.save(path)
    return path


def _answer(qid: str, text: str, **extra: Any) -> dict[str, Any]:
    return {"question_id": qid, "status": "answered", "answer": text, "confidence": 0.9, **extra}


def _extract(
    pages: list[Path],
    client: FakeClient,
    question_types: dict[str, str] | None = None,
) -> ExtractionOutcome:
    return ScriptExtractor(vlm_client=client).extract_pages(pages, QUESTION_IDS, question_types)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "extraction_cache_dir", tmp_path / "cache")


def test_one_call_per_page_and_continuation_is_appended(tmp_path: Path) -> None:
    pages = [_page(tmp_path / "p1.png", 0), _page(tmp_path / "p2.png", 1)]
    client = FakeClient(
        [
            {
                "raw_text": "1. A\n2. first part",
                "answers": [_answer("Q1", "A"), _answer("Q2", "first part")],
            },
            {
                "raw_text": "more of two\n3. C",
                "answers": [
                    _answer("Q2", "more of two", continues_previous=True),
                    _answer("Q3", "C"),
                ],
            },
        ]
    )

    outcome = _extract(pages, client, {"Q1": "mcq", "Q2": "short_answer"})

    assert len(client.prompts) == 2  # one structured call per page, no second stage
    assert outcome.questions == {"Q1": "A", "Q2": "first part\nmore of two", "Q3": "C"}
    assert outcome.question_pages == {"Q1": 1, "Q2": 1, "Q3": 2}
    assert outcome.conflicts == []
    assert outcome.question_confidences["Q2"] == 0.9
    assert outcome.model.startswith("vlm:")
    assert "Q1 (mcq)" in client.prompts[0]
    assert "previous page's last answer was for Q2" in client.prompts[1]


def test_second_run_is_served_from_cache(tmp_path: Path) -> None:
    pages = [_page(tmp_path / "p1.png", 0)]
    _extract(pages, FakeClient([{"raw_text": "1. B", "answers": [_answer("Q1", "B")]}]))

    second = FakeClient([])  # would raise IndexError if the model were called
    outcome = _extract(pages, second)

    assert second.prompts == []
    assert outcome.questions["Q1"] == "B"


def test_script_extractor_uses_the_injected_client_and_model(tmp_path: Path) -> None:
    page = _page(tmp_path / "p1.png", 0)
    client = FakeClient([{"raw_text": "1. A", "answers": [_answer("Q1", "A")]}])

    outcome = ScriptExtractor(
        vlm_client=client,
        config=Settings(nvidia_vlm_model="test-vlm", extraction_cache=False, _env_file=None),
    ).extract_pages([page], QUESTION_IDS)

    assert outcome.questions["Q1"] == "A"
    assert outcome.model == "vlm:test-vlm"
    assert client.prompts


def test_illegible_answer_is_flagged_and_confidence_capped(tmp_path: Path) -> None:
    pages = [_page(tmp_path / "p1.png", 0)]
    client = FakeClient(
        [
            {
                "raw_text": "1. [illegible]",
                "answers": [
                    {
                        "question_id": "Q1",
                        "status": "illegible",
                        "answer": "maybe B",
                        "confidence": 0.95,
                    }
                ],
            }
        ]
    )

    outcome = _extract(pages, client)

    assert outcome.questions["Q1"] == "maybe B"
    assert outcome.question_confidences["Q1"] <= 0.3
    illegible = [c["question_id"] for c in outcome.conflicts if c.get("kind") == "illegible"]
    assert illegible == ["Q1"]


def test_completeness_retry_recovers_question_missing_from_answers(tmp_path: Path) -> None:
    pages = [_page(tmp_path / "p1.png", 0)]
    client = FakeClient(
        [
            {"raw_text": "1. A\n2. B", "answers": [_answer("Q1", "A")]},
            {"raw_text": "1. A\n2. B", "answers": [_answer("Q1", "A"), _answer("Q2", "B")]},
        ]
    )

    outcome = _extract(pages, client)

    assert len(client.prompts) == 2
    assert "Q2" in client.prompts[1]
    # Q3 was never mentioned anywhere -> empty placeholder
    assert outcome.questions == {"Q1": "A", "Q2": "B", "Q3": ""}


def test_repeated_true_false_answers_are_not_flagged_as_fabrication(tmp_path: Path) -> None:
    pages = [_page(tmp_path / "p1.png", 0)]
    client = FakeClient(
        [
            {
                "raw_text": "1. True\n2. True\n3. True",
                "answers": [_answer("Q1", "True"), _answer("Q2", "True"), _answer("Q3", "True")],
            }
        ]
    )

    outcome = _extract(pages, client)

    assert outcome.questions == {"Q1": "True", "Q2": "True", "Q3": "True"}
    assert outcome.conflicts == []


def test_model_returning_answers_as_mapping_is_accepted() -> None:
    parsed = PageExtraction.model_validate({"raw_text": None, "answers": {"Q1": "A", "2": 7}})

    assert parsed.raw_text == ""
    assert [(a.question_id, a.answer) for a in parsed.answers] == [("Q1", "A"), ("2", "7")]
