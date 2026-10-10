from __future__ import annotations

from pathlib import Path

from online_dekanat.ocr.base import OCRResult


class PlainTextOCRClient:
    """Test/demo OCR adapter that reads text directly from a .txt file or sidecar."""

    def recognize(self, file_path: str | Path) -> OCRResult:
        path = Path(file_path)
        if path.suffix.lower() == ".txt":
            text_path = path
        else:
            text_path = path.with_suffix(".txt")
        if not text_path.exists():
            raise FileNotFoundError(f"Text OCR mode expected {text_path}")
        return OCRResult(text=text_path.read_text(encoding="utf-8"), engine="plain-text", pages=1)
