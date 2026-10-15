from pathlib import Path

from PIL import Image, ImageDraw

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRRegion
from online_dekanat.quality import (
    normalize_orientation,
    prepare_for_ocr,
    rescale_ocr_regions,
)


def test_normalize_orientation_rotates_pages_before_visual_checks(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "rotated.png"
    image = Image.new("RGB", (120, 70), "white")
    ImageDraw.Draw(image).rectangle((5, 5, 24, 24), fill="black")
    image.save(source)
    monkeypatch.setattr("online_dekanat.quality._load_pytesseract", lambda: object())
    monkeypatch.setattr("online_dekanat.quality._page_rotation", lambda page, ocr: 90)

    oriented = normalize_orientation(source)

    try:
        pages = load_pages(oriented)
        assert pages[0].size == (70, 120)
        assert pages[0].getpixel((10, 110)) == (0, 0, 0)
    finally:
        if oriented != source:
            oriented.unlink(missing_ok=True)


def test_load_pages_reads_all_frames_in_oriented_tiff(tmp_path: Path) -> None:
    source = tmp_path / "pages.tiff"
    first = Image.new("RGB", (40, 60), "white")
    second = Image.new("RGB", (60, 40), "black")
    first.save(source, format="TIFF", save_all=True, append_images=[second])

    pages = load_pages(source)

    assert [page.size for page in pages] == [(40, 60), (60, 40)]


def test_prepare_for_ocr_upscales_low_resolution_images(tmp_path: Path) -> None:
    source = tmp_path / "small.png"
    Image.new("RGB", (300, 200), "white").save(source)

    enhanced = prepare_for_ocr(source, {"status": "good", "issues": ["low_resolution"]})

    try:
        assert enhanced != source
        assert load_pages(enhanced)[0].size == (600, 400)
    finally:
        enhanced.unlink(missing_ok=True)


def test_prepare_for_ocr_preserves_tones_on_overexposed_images(tmp_path: Path) -> None:
    source = tmp_path / "overexposed.png"
    Image.new("RGB", (300, 200), (248, 248, 248)).save(source)

    prepared = prepare_for_ocr(
        source, {"status": "warning", "issues": ["low_resolution", "overexposed"]}
    )

    try:
        page = load_pages(prepared)[0]
        assert page.size == (600, 400)
        assert page.getpixel((20, 20)) == (248, 248, 248)
    finally:
        prepared.unlink(missing_ok=True)


def test_rescale_ocr_regions_maps_upscaled_coordinates_to_source(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    processed = tmp_path / "processed.png"
    Image.new("RGB", (300, 200), "white").save(source)
    Image.new("RGB", (600, 400), "white").save(processed)
    region = OCRRegion("Иванов", (200, 100, 400, 140), page=0, confidence=0.9)

    mapped = rescale_ocr_regions([region], processed, source)

    assert mapped == [
        OCRRegion("Иванов", (100, 50, 200, 70), page=0, confidence=0.9)
    ]
