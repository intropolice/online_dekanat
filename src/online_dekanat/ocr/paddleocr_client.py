from __future__ import annotations

from pathlib import Path
from typing import Any

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRResult


class PaddleOCRClient:
    """PaddleOCR adapter supporting both legacy and current PaddleOCR APIs."""

    def __init__(self, language: str = "cyrillic", use_gpu: bool = False) -> None:
        self.language = language
        self.use_gpu = use_gpu
        self._engine: Any | None = None

    def recognize(self, file_path: str | Path) -> OCRResult:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("PaddleOCR is not installed. Install with: python -m pip install -e .[paddleocr]") from exc
        if self._engine is None:
            self._engine = self._create_engine(PaddleOCR)
        texts: list[str] = []
        for page in load_pages(file_path):
            texts.append(self._recognize_page(page))
        return OCRResult(text="\n\n".join(texts).strip(), engine="paddleocr", pages=len(texts))

    def _create_engine(self, paddle_ocr: Any) -> Any:
        attempts = [
            {"lang": self.language, "use_doc_orientation_classify": False, "use_doc_unwarping": False, "use_textline_orientation": False},
            {"lang": self.language, "use_angle_cls": True, "use_gpu": self.use_gpu, "show_log": False},
            {"lang": "en", "use_gpu": self.use_gpu, "show_log": False},
        ]
        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                return paddle_ocr(**kwargs)
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"PaddleOCR could not be initialized: {last_error}") from last_error

    def _recognize_page(self, page: Any) -> str:
        import numpy as np

        image = np.asarray(page)
        if hasattr(self._engine, "predict"):
            try:
                result = self._engine.predict(input=image)
            except TypeError:
                result = self._engine.predict(image)
            return _extract_predict_text(result)
        result = self._engine.ocr(image, cls=True)
        return _extract_legacy_text(result)


def _extract_predict_text(result: Any) -> str:
    lines: list[str] = []
    for item in result or []:
        data = getattr(item, "json", None)
        if callable(data):
            data = data()
        if isinstance(data, str):
            import json
            data = json.loads(data)
        if isinstance(data, dict):
            data = data.get("res", data)
            texts = data.get("rec_texts", []) if isinstance(data, dict) else []
            lines.extend(str(text).strip() for text in texts if str(text).strip())
    return "\n".join(lines)


def _extract_legacy_text(result: Any) -> str:
    lines: list[str] = []
    for page in result or []:
        for row in page or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                value = row[1]
                if isinstance(value, (list, tuple)) and value:
                    text = str(value[0]).strip()
                    if text:
                        lines.append(text)
    return "\n".join(lines)
