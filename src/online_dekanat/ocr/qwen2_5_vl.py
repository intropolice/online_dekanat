from __future__ import annotations

from pathlib import Path
from typing import Any

from online_dekanat.io import load_pages
from online_dekanat.ocr.base import OCRResult


class Qwen25VLOCRClient:
    """Local OCR adapter backed by Qwen2.5-VL-3B-Instruct."""

    def __init__(self, model_name: str = "Qwen/Qwen2.5-VL-3B-Instruct", max_new_tokens: int = 2048) -> None:
        self.model_name = model_name
        self.max_new_tokens = max_new_tokens
        self._model: Any | None = None
        self._processor: Any | None = None
        self._torch: Any | None = None

    def recognize(self, file_path: str | Path) -> OCRResult:
        pages = load_pages(file_path)
        self._ensure_loaded()

        texts = [self._recognize_page(page) for page in pages]
        return OCRResult(text="\n\n".join(texts).strip(), engine="qwen2.5-vl-3b", pages=len(pages))

    def _recognize_page(self, page: Any) -> str:
        from qwen_vl_utils import process_vision_info

        torch = self._torch
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "image": page},
                {"type": "text", "text": "Распознай весь текст документа. Сохрани русский язык, порядок строк, номера и даты. Не добавляй пояснений."},
            ],
        }]
        prompt = self._processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self._processor(
            text=[prompt], images=image_inputs, videos=video_inputs, padding=True, return_tensors="pt"
        )
        inputs = inputs.to(self._model.device)
        with torch.inference_mode():
            generated = self._model.generate(**inputs, max_new_tokens=self.max_new_tokens, do_sample=False)
        generated = [output[len(source):] for source, output in zip(inputs.input_ids, generated)]
        return self._processor.batch_decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0].strip()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
            import qwen_vl_utils  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Qwen OCR dependencies are missing. Install with: python -m pip install -e .[qwen]"
            ) from exc

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        try:
            processor = AutoProcessor.from_pretrained(self.model_name)
            model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                self.model_name, torch_dtype=dtype, low_cpu_mem_usage=True
            )
            model = model.to(device).eval()
        except Exception as exc:
            raise RuntimeError(
                f"Could not load {self.model_name}. Check network access, available memory, and installed Qwen dependencies: {exc}"
            ) from exc
        self._torch = torch
        self._processor = processor
        self._model = model
