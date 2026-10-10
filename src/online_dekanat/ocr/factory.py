from __future__ import annotations

from online_dekanat.ocr.base import OCRClient, OCRResult
from online_dekanat.ocr.easyocr_client import EasyOCRClient
from online_dekanat.ocr.plain_text import PlainTextOCRClient
from online_dekanat.ocr.paddleocr_client import PaddleOCRClient
from online_dekanat.ocr.tesseract import TesseractOCRClient


def create_ocr_client(name: str) -> OCRClient:
    normalized = name.lower().strip()
    if normalized in {"auto", "automatic", "default"}:
        return AutomaticOCRClient()
    if normalized == "tesseract":
        return TesseractOCRClient()
    if normalized in {"easyocr", "easy"}:
        return EasyOCRClient()
    if normalized in {"paddleocr", "paddle"}:
        return PaddleOCRClient()
    if normalized in {"text", "plain-text", "plain"}:
        return PlainTextOCRClient()
    raise ValueError(f"Unknown OCR engine: {name}")


class AutomaticOCRClient:
    """Try the best local OCR backend and fall back without user configuration."""

    def recognize(self, file_path: str) -> OCRResult:
        errors: list[str] = []
        for client in (PaddleOCRClient(), EasyOCRClient(), TesseractOCRClient()):
            try:
                return client.recognize(file_path)
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("Не удалось запустить автоматический OCR: " + " | ".join(errors))
