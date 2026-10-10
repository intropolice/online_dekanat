from __future__ import annotations

from online_dekanat.ocr.base import OCRClient
from online_dekanat.ocr.base import OCRResult
from online_dekanat.ocr.plain_text import PlainTextOCRClient
from online_dekanat.ocr.qwen2_5_vl import Qwen25VLOCRClient
from online_dekanat.ocr.tesseract import TesseractOCRClient


def create_ocr_client(name: str) -> OCRClient:
    normalized = name.lower().strip()
    if normalized in {"auto", "automatic", "default"}:
        return AutomaticOCRClient()
    if normalized in {"qwen", "qwen2.5", "qwen2.5-vl", "qwen2.5-vl-3b"}:
        return Qwen25VLOCRClient()
    if normalized == "tesseract":
        return TesseractOCRClient()
    if normalized in {"text", "plain-text", "plain"}:
        return PlainTextOCRClient()
    raise ValueError(f"Unknown OCR engine: {name}")


class AutomaticOCRClient:
    """Try the best local OCR backend and fall back without user configuration."""

    def recognize(self, file_path: str) -> OCRResult:
        errors: list[str] = []
        for client in (Qwen25VLOCRClient(), TesseractOCRClient()):
            try:
                return client.recognize(file_path)
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("Не удалось запустить автоматический OCR: " + " | ".join(errors))
