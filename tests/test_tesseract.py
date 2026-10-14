import sys
from types import SimpleNamespace

from PIL import Image

from online_dekanat.ocr.tesseract import TesseractOCRClient


def test_tesseract_uses_sparse_layout_for_small_pages(monkeypatch) -> None:
    configs: list[str] = []

    def image_to_string(page, *, lang: str, config: str) -> str:
        configs.append(config)
        return "recognized text"

    def image_to_data(page, *, lang: str, config: str, output_type):
        configs.append(config)
        return {"text": []}

    pytesseract = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_string=image_to_string,
        image_to_data=image_to_data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)
    monkeypatch.setattr(
        "online_dekanat.ocr.tesseract.load_pages",
        lambda path: [Image.new("RGB", (654, 926))],
    )

    result = TesseractOCRClient().recognize("small.png")

    assert result.text == "recognized text"
    assert configs == ["--psm 11", "--psm 11"]

    configs.clear()
    monkeypatch.setattr(
        "online_dekanat.ocr.tesseract.load_pages",
        lambda path: [Image.new("RGB", (1200, 1600))],
    )
    TesseractOCRClient().recognize("large.png")

    assert configs == ["--psm 6", "--psm 6"]
