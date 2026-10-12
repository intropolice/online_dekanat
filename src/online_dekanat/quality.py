from __future__ import annotations

from pathlib import Path
import os
import tempfile
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from online_dekanat.io import IMAGE_EXTENSIONS, load_pages


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


def prepare_for_ocr(path: str | Path, quality: dict[str, Any]) -> Path:
    """Create an enhanced temporary image for hard photo inputs."""
    source = Path(path)
    if source.suffix.lower() not in IMAGE_EXTENSIONS or quality.get("status") not in {"warning", "bad"}:
        return source
    from PIL import ImageEnhance, ImageOps

    with Image.open(source) as opened:
        image = ImageOps.grayscale(opened).convert("RGB")
        image = ImageOps.autocontrast(image, cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.25)
        image = image.filter(ImageFilter.MedianFilter(size=3))
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        handle, name = tempfile.mkstemp(prefix="dekanat-enhanced-", suffix=".png")
        os.close(handle)
        image.save(name, format="PNG")
    return Path(name)
