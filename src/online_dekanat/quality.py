from __future__ import annotations

import logging
from pathlib import Path
import os
import re
import tempfile
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from online_dekanat.io import IMAGE_EXTENSIONS, load_pages

logger = logging.getLogger(__name__)


def assess_document(path: str | Path) -> dict[str, Any]:
    """Measure image quality before OCR and return actionable diagnostics."""
    source = Path(path)
    if source.suffix.lower() == ".txt":
        return {"status": "not_applicable", "score": 1.0, "issues": [], "pages": 1}
    pages = load_pages(source)
    if not pages:
        return {"status": "bad", "score": 0.0, "issues": ["empty_document"], "pages": 0}
    page_reports: list[dict[str, Any]] = []
    for page in pages:
        gray = page.convert("L")
        width, height = page.size
        brightness = float(ImageStat.Stat(gray).mean[0])
        contrast = float(ImageStat.Stat(gray).stddev[0])
        # A blurred copy approximates the Laplacian variance without OpenCV.
        edges = gray.filter(ImageFilter.FIND_EDGES)
        sharpness = float(ImageStat.Stat(edges).var[0])
        page_reports.append({"width": width, "height": height, "brightness": round(brightness, 2), "contrast": round(contrast, 2), "sharpness": round(sharpness, 2)})
    issues: list[str] = []
    min_width = min(report["width"] for report in page_reports)
    min_height = min(report["height"] for report in page_reports)
    avg_brightness = sum(report["brightness"] for report in page_reports) / len(page_reports)
    avg_contrast = sum(report["contrast"] for report in page_reports) / len(page_reports)
    avg_sharpness = sum(report["sharpness"] for report in page_reports) / len(page_reports)
    if min_width < 900 or min_height < 700:
        issues.append("low_resolution")
    if avg_brightness < 45:
        issues.append("too_dark")
    elif avg_brightness > 235:
        issues.append("overexposed")
    if avg_contrast < 18:
        issues.append("low_contrast")
    if avg_sharpness < 25:
        issues.append("blurred")
    score = 1.0
    score -= 0.25 * min(2, sum(issue in issues for issue in ("low_resolution", "blurred")))
    score -= 0.15 * min(2, sum(issue in issues for issue in ("too_dark", "overexposed", "low_contrast")))
    score = max(0.0, round(score, 2))
    status = "good" if score >= 0.75 else "warning" if score >= 0.45 else "bad"
    return {"status": status, "score": score, "issues": issues, "pages": len(pages), "page_metrics": page_reports}


def normalize_orientation(path: str | Path) -> Path:
    """Rotate document pages upright before OCR and visual field detection."""
    source = Path(path)
    if source.suffix.lower() == ".txt":
        return source

    pages = load_pages(source)
    rotations: list[int] = []
    pytesseract = _load_pytesseract()
    if pytesseract is None:
        logger.warning("Skipping automatic page orientation: install the tesseract OCR extra.")
        return source

    for page in pages:
        try:
            rotations.append(_page_rotation(page, pytesseract))
        except pytesseract.TesseractNotFoundError:
            logger.warning("Skipping automatic page orientation: Tesseract is not installed.")
            return source

    if not any(rotations):
        return source

    oriented_pages = [
        page.rotate(rotation, expand=True) if rotation else page
        for page, rotation in zip(pages, rotations)
    ]
    handle, name = tempfile.mkstemp(prefix="dekanat-oriented-", suffix=".tiff")
    os.close(handle)
    oriented_pages[0].save(
        name,
        format="TIFF",
        save_all=True,
        append_images=oriented_pages[1:],
        compression="tiff_deflate",
    )
    return Path(name)


def _load_pytesseract() -> Any | None:
    try:
        import pytesseract
    except ImportError:
        return None
    return pytesseract


def _page_rotation(page: Image.Image, pytesseract: Any) -> int:
    analysis_page = page.copy()
    analysis_page.thumbnail((1600, 1600))
    try:
        osd = pytesseract.image_to_osd(analysis_page, lang="osd", config="--psm 0")
    except pytesseract.TesseractError:
        osd = ""
    rotate_match = re.search(r"^Rotate:\s*(\d+)", osd, re.MULTILINE)
    confidence_match = re.search(r"^Orientation confidence:\s*([\d.]+)", osd, re.MULTILINE)
    if rotate_match and confidence_match and float(confidence_match.group(1)) >= 5:
        return (-int(rotate_match.group(1))) % 360

    scores: dict[int, float] = {}
    for rotation in (0, 90, 180, 270):
        candidate = analysis_page.rotate(rotation, expand=True) if rotation else analysis_page
        data = pytesseract.image_to_data(
            candidate,
            lang="rus+eng",
            config="--psm 6",
            output_type=pytesseract.Output.DICT,
        )
        scores[rotation] = sum(
            float(confidence)
            for confidence in data["conf"]
            if confidence != "-1" and float(confidence) > 0
        )

    best_rotation = max(scores, key=scores.get)
    if best_rotation and scores[best_rotation] > scores[0] * 1.25:
        return best_rotation
    return 0


def prepare_for_ocr(path: str | Path, quality: dict[str, Any]) -> Path:
    """Create an enhanced temporary image for hard photo inputs."""
    source = Path(path)
    if source.suffix.lower() not in IMAGE_EXTENSIONS or quality.get("status") not in {"warning", "bad"}:
        return source
    from PIL import ImageEnhance, ImageOps

    pages = load_pages(source)
    enhanced_pages: list[Image.Image] = []
    for page in pages:
        image = ImageOps.grayscale(page).convert("RGB")
        image = ImageOps.autocontrast(image, cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.25)
        if min(image.size) >= 900:
            image = image.filter(ImageFilter.MedianFilter(size=3))
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        enhanced_pages.append(image)
    handle, name = tempfile.mkstemp(prefix="dekanat-enhanced-", suffix=".tiff")
    os.close(handle)
    enhanced_pages[0].save(
        name,
        format="TIFF",
        save_all=True,
        append_images=enhanced_pages[1:],
        compression="tiff_deflate",
    )
    return Path(name)
