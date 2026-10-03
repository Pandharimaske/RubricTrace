from pathlib import Path

import pymupdf


def pdf_to_images(pdf_path: Path, output_dir: Path, zoom: float = 2.0) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    image_paths: list[Path] = []

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        image_path = output_dir / f"{pdf_path.stem}_page_{page_index + 1}.png"
        pixmap.save(image_path)
        image_paths.append(image_path)

    return image_paths
