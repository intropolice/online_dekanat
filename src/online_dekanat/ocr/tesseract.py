from __future__ import annotations

from pathlib import Path

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRRegion, OCRResult


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
        regions: list[OCRRegion] = []
        pages = load_pages(file_path)
        for page_index, page in enumerate(pages):
            texts.append(pytesseract.image_to_string(page, lang=self.lang, config=self.config))
            data = pytesseract.image_to_data(page, lang=self.lang, config=self.config, output_type=pytesseract.Output.DICT)
            for index, raw in enumerate(data.get("text", [])):
                value = str(raw).strip()
                try:
                    confidence = float(data["conf"][index])
                except (KeyError, ValueError, TypeError):
                    confidence = None
                if not value or confidence is not None and confidence < 0:
                    continue
                regions.append(OCRRegion(value, (float(data["left"][index]), float(data["top"][index]), float(data["left"][index] + data["width"][index]), float(data["top"][index] + data["height"][index])), page_index, confidence))
        return OCRResult(text="\n\n".join(texts).strip(), engine="tesseract", pages=len(pages), regions=regions)
