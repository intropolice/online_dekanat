from __future__ import annotations

from pathlib import Path
from typing import Any

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRResult


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
        import numpy as np
        for page in load_pages(file_path):
            lines = self._reader.readtext(np.asarray(page), detail=0, paragraph=True)
            texts.append("\n".join(str(line).strip() for line in lines if str(line).strip()))
        return OCRResult(text="\n\n".join(texts).strip(), engine="easyocr", pages=len(texts))
