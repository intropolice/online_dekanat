from __future__ import annotations

from pathlib import Path

from online_dekanat.answering import answer_question
from online_dekanat.config import load_document_types, load_regulations
from online_dekanat.extraction import extract_card
from online_dekanat.models import PipelineResult
from online_dekanat.ocr.base import OCRClient
from online_dekanat.ocr.factory import create_ocr_client
from online_dekanat.secure_storage import EncryptedStorage


class DeanOfficePipeline:
    def __init__(self, ocr_client: OCRClient | None = None, ocr_name: str = "auto") -> None:
        self.document_types = load_document_types()
        self.regulations = load_regulations()
        self.ocr_client = ocr_client or create_ocr_client(ocr_name)
        self.storage = EncryptedStorage()

    def process(self, file_path: str | Path, question: str, doc_type: str | None = None) -> PipelineResult:
        with self.storage.decrypted_temporary_file(file_path) as readable_path:
            ocr_result = self.ocr_client.recognize(readable_path)
        card = extract_card(ocr_result.text, self.document_types, doc_type=doc_type)
        answer = answer_question(question, card, self.regulations)
        return PipelineResult(card=card, answer=answer)
