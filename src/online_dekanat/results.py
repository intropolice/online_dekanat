from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def append_result(payload: dict[str, Any], path: str | Path | None = None) -> None:
    target = Path(path or os.getenv("ONLINE_DEKANAT_RESULTS_PATH", "results.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        records = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
        if not isinstance(records, list):
            records = []
    except (OSError, json.JSONDecodeError):
        records = []
    records.append(payload)
    fd, temporary = tempfile.mkstemp(prefix="results-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def submission_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the submission ledger useful without duplicating student PII."""
    card = payload.get("card", {})
    answer = payload.get("answer", {})
    return {
        "result_id": payload.get("result_id"),
        "processed_at": payload.get("processed_at"),
        "document_type": card.get("doc_type"),
        "document_label": card.get("doc_label"),
        "answer": answer.get("text"),
        "confidence": answer.get("confidence"),
        "missing_fields": answer.get("missing_fields", []),
        "quality": payload.get("quality", {}),
    }
