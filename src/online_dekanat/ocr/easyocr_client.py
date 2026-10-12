from __future__ import annotations

from pathlib import Path
from typing import Any

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRRegion, OCRResult


class EasyOCRClient:
    """CPU/GPU OCR adapter for Russian and English documents."""

    def __init__(self, languages: list[str] | None = None, gpu: bool = False) -> None:
        self.languages = languages or ["ru", "en"]
        self.gpu = gpu
        self._reader: Any | None = None

    def recognize(self, file_path: str | Path) -> OCRResult:
        try:
            import easyocr
        except ImportError as exc:
            raise RuntimeError("EasyOCR is not installed. Install with: python -m pip install -e .[easyocr]") from exc
        if self._reader is None:
            self._reader = easyocr.Reader(self.languages, gpu=self.gpu, verbose=False)
        texts: list[str] = []
        regions: list[OCRRegion] = []
        import numpy as np
        for page_index, page in enumerate(load_pages(file_path)):
            lines = self._reader.readtext(np.asarray(page), detail=1, paragraph=False)
            page_text: list[str] = []
            for bbox, value, confidence in lines:
                value = str(value).strip()
                if not value:
                    continue
                page_text.append(value)
                xs = [point[0] for point in bbox]
                ys = [point[1] for point in bbox]
                regions.append(OCRRegion(value, (min(xs), min(ys), max(xs), max(ys)), page_index, float(confidence)))
            texts.append("\n".join(page_text))
        return OCRResult(text="\n\n".join(texts).strip(), engine="easyocr", pages=len(texts), regions=regions)
