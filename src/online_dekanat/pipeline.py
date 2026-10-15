from __future__ import annotations

from pathlib import Path
import os

from online_dekanat.answering import answer_question
from online_dekanat.config import load_document_types, load_regulations, DEFAULT_CONFIG_DIR
from online_dekanat.extraction import classify_document, extract_card
from online_dekanat.highlight import apply_visual_presence, create_highlights
from online_dekanat.models import PipelineResult
from online_dekanat.ocr.base import OCRClient
from online_dekanat.ocr.factory import create_ocr_client
from online_dekanat.quality import (
    assess_document,
    normalize_orientation,
    prepare_for_ocr,
    rescale_ocr_regions,
)
from online_dekanat.database import StudentDatabase
from online_dekanat.results import append_result, submission_record
from online_dekanat.secure_storage import EncryptedStorage


class DeanOfficePipeline:
    def __init__(self, ocr_client: OCRClient | None = None, ocr_name: str = "auto") -> None:
        # ИСПРАВЛЕНИЕ ДЛЯ DOCKER: Проверяем, существует ли папка конфигурации.
        # Если базовый путь неверен, перенаправляем в рабочую директорию контейнера.
        config_path = DEFAULT_CONFIG_DIR
        if not config_path.exists():
            config_path = Path(os.getcwd()) / "config"

        self.document_types = load_document_types(config_dir=config_path)
        self.regulations = load_regulations(config_dir=config_path)
        self.ocr_client = ocr_client or create_ocr_client(ocr_name)
        self.storage = EncryptedStorage()
        self.database = StudentDatabase()

    def process(self, file_path: str | Path, question: str, doc_type: str | None = None) -> PipelineResult:
        with self.storage.decrypted_temporary_file(file_path) as readable_path:
            quality = assess_document(readable_path)
            visual_path = normalize_orientation(readable_path)
            try:
                ocr_path = prepare_for_ocr(visual_path, quality)
                try:
                    ocr_result = self.ocr_client.recognize(ocr_path)
                    regions = rescale_ocr_regions(
                        ocr_result.regions, ocr_path, visual_path
                    )
                finally:
                    if ocr_path != visual_path:
                        ocr_path.unlink(missing_ok=True)
                selected_type = doc_type or classify_document(
                    ocr_result.text, self.document_types
                )
                card = extract_card(
                    ocr_result.text, self.document_types, doc_type=selected_type
                )
                apply_visual_presence(visual_path, regions, card)
                highlights = create_highlights(visual_path, regions, card)
            finally:
                if visual_path != readable_path:
                    visual_path.unlink(missing_ok=True)
        answer = answer_question(question, card, self.regulations)
        result = PipelineResult(card=card, answer=answer, quality=quality, highlights=highlights)
        result_id = self.database.save_result(result)
        result.result_id = result_id
        from datetime import datetime, timezone
        result.processed_at = datetime.now(timezone.utc).isoformat()
        append_result(submission_record(result.to_dict()))
        return result
