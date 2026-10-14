from pathlib import Path

from PIL import Image, ImageDraw

from online_dekanat.io import load_pages
from online_dekanat.quality import normalize_orientation


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
