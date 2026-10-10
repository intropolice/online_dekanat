from __future__ import annotations

from pathlib import Path

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRResult


class TesseractOCRClient:
    def __init__(self, lang: str = "rus+eng", config: str = "--psm 6") -> None:
        self.lang = lang
        self.config = config

    def recognize(self, file_path: str | Path) -> OCRResult:
        try:
            import pytesseract
        except ImportError as exc:
            raise RuntimeError("Tesseract adapter requires pytesseract. Install with: python -m pip install -e .[tesseract]") from exc

        texts: list[str] = []
        pages = load_pages(file_path)
        for page in pages:
            texts.append(pytesseract.image_to_string(page, lang=self.lang, config=self.config))
        return OCRResult(text="\n\n".join(texts).strip(), engine="tesseract", pages=len(pages))
