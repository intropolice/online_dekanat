from __future__ import annotations

from pathlib import Path
from typing import Any

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRRegion, OCRResult


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
        regions: list[OCRRegion] = []
        for page_index, page in enumerate(load_pages(file_path)):
            page_text, page_regions = self._recognize_page(page, page_index)
            texts.append(page_text)
            regions.extend(page_regions)
        return OCRResult(text="\n\n".join(texts).strip(), engine="paddleocr", pages=len(texts), regions=regions)

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

    def _recognize_page(self, page: Any, page_index: int = 0) -> tuple[str, list[OCRRegion]]:
        import numpy as np

        image = np.asarray(page)
        if hasattr(self._engine, "predict"):
            try:
                result = self._engine.predict(input=image)
            except TypeError:
                result = self._engine.predict(image)
            return _extract_predict_text(result, page_index)
        result = self._engine.ocr(image, cls=True)
        return _extract_legacy_text(result, page_index)


def _extract_predict_text(result: Any, page_index: int = 0) -> tuple[str, list[OCRRegion]]:
    lines: list[str] = []
    regions: list[OCRRegion] = []
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
            boxes = data.get("rec_boxes", []) if isinstance(data, dict) else []
            for index, text in enumerate(texts):
                value = str(text).strip()
                if not value:
                    continue
                lines.append(value)
                if index < len(boxes):
                    box = boxes[index]
                    regions.append(OCRRegion(value, (float(box[0]), float(box[1]), float(box[2]), float(box[3])), page_index))
    return "\n".join(lines), regions


def _extract_legacy_text(result: Any, page_index: int = 0) -> tuple[str, list[OCRRegion]]:
    lines: list[str] = []
    regions: list[OCRRegion] = []
    for page in result or []:
        for row in page or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                value = row[1]
                if isinstance(value, (list, tuple)) and value:
                    text = str(value[0]).strip()
                    if text:
                        lines.append(text)
                        box = row[0] if row else []
                        if box:
                            xs = [point[0] for point in box]
                            ys = [point[1] for point in box]
                            regions.append(OCRRegion(text, (min(xs), min(ys), max(xs), max(ys)), page_index, float(value[1]) if len(value) > 1 else None))
    return "\n".join(lines), regions
