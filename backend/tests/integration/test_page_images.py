"""Page images for the review screen, including scripts that were inserted without rendering."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from backend.app.container import Container
from backend.app.main import create_app
from fastapi.testclient import TestClient

PNG_MAGIC = b"\x89PNG"


@pytest.fixture
def container(make_container: Callable[..., Container]) -> Container:
    return make_container()


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as test_client:
        yield test_client


def _two_page_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for number in (1, 2):
        page = doc.new_page()
        page.insert_text((72, 72), f"Answer sheet page {number}")
    doc.save(path)
    return path


def _insert_unrendered_script(container: Container, pdf: Path) -> str:
    """A script whose original is in the upload directory but whose pages were never rendered
    (what an import produces). When `pdf` does not exist the original is simply missing."""
    container.config.ensure_data_dirs()
    exam = container.exams.create("Imported")
    script_id = str(uuid4())
    if pdf.is_file():
        shutil.copy(pdf, container.config.upload_dir / f"{script_id}.pdf")
    container.scripts.insert(
        script_id, "student-1", pdf.name, str(pdf), exam["exam_id"], student_name="Student One"
    )
    return script_id


def test_page_is_rendered_on_first_request(
    client: TestClient, container: Container, tmp_path: Path
) -> None:
    script_id = _insert_unrendered_script(container, _two_page_pdf(tmp_path / "paper.pdf"))
    assert not (container.config.page_image_dir / script_id).exists()

    first = client.get(f"/api/scripts/{script_id}/pages/1/image")
    second = client.get(f"/api/scripts/{script_id}/pages/2/image")

    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content.startswith(PNG_MAGIC)
    assert second.status_code == 200
    assert (container.scripts.get(script_id) or {})["page_count"] == 2


def test_page_beyond_the_end_is_a_404_not_a_crash(
    client: TestClient, container: Container, tmp_path: Path
) -> None:
    script_id = _insert_unrendered_script(container, _two_page_pdf(tmp_path / "paper.pdf"))

    response = client.get(f"/api/scripts/{script_id}/pages/9/image")

    assert response.status_code == 404
    assert "2 pages" in response.json()["detail"]


def test_missing_original_file_explains_why_there_is_no_image(
    client: TestClient, container: Container, tmp_path: Path
) -> None:
    script_id = _insert_unrendered_script(container, tmp_path / "gone.pdf")

    response = client.get(f"/api/scripts/{script_id}/pages/1/image")

    assert response.status_code == 404
    assert "original file is missing" in response.json()["detail"]


def test_unknown_script_is_a_404(client: TestClient) -> None:
    assert client.get("/api/scripts/nope/pages/1/image").status_code == 404
