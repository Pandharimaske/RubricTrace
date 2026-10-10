"""
Blank / near-blank page detection for the VLM extraction pipeline.

Some rendered PDF pages have nothing real on them — a genuinely blank page, or
faint bleed-through ink from the opposite side of a scanned sheet. Sending
those to the VLM anyway is expensive and, worse, some vision models will
happily invent plausible-looking exam content rather than admit there's
nothing to read (fabrication, not extraction). This module screens pages out
before they ever reach the VLM, using a cheap image heuristic instead of
relying on the model to self-report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat


@dataclass(frozen=True)
class BlankPageResult:
    is_blank: bool
    ink_ratio: float
    reason: str


def is_blank_page(
    image_path: Path,
    ink_ratio_threshold: float = 0.003,
    dark_pixel_threshold: int = 180,
    stddev_threshold: float = 12.0,
) -> BlankPageResult:
    """
    Heuristic check for a blank or near-blank (bleed-through) rendered page.

    Converts the page to grayscale and measures the fraction of pixels dark
    enough to plausibly be ink. Real handwritten answer pages have a small
    but clearly nonzero ink fraction; blank pages and faint bleed-through
    ghosting from the reverse side of the paper fall well below it.

    Tune `ink_ratio_threshold` against a few known blank/near-blank pages
    from your own dataset before trusting the default — scan quality and
    render zoom level both shift where the real cutoff sits. (RubricTrace
    renders at zoom=2.0 in pdf.py; if that changes, re-check the threshold.)
    """
    with Image.open(image_path) as img:
        gray = img.convert("L")
        gray = gray.filter(ImageFilter.GaussianBlur(radius=1))

        histogram = gray.histogram()
        total_pixels = sum(histogram)
        dark_pixels = sum(histogram[:dark_pixel_threshold])
        ink_ratio = dark_pixels / total_pixels if total_pixels else 0.0

        stat = ImageStat.Stat(gray)
        stdev = stat.stddev[0] if stat.stddev else 0.0

    blank = ink_ratio < ink_ratio_threshold and stdev < stddev_threshold
    reason = (
        f"ink_ratio={ink_ratio:.5f} (threshold={ink_ratio_threshold}), "
        f"stddev={stdev:.2f} (threshold={stddev_threshold})"
    )
    return BlankPageResult(is_blank=blank, ink_ratio=ink_ratio, reason=reason)
