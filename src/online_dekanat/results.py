from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def append_result(payload: dict[str, Any], path: str | Path | None = None) -> bool:
    target = Path(path or os.getenv("ONLINE_DEKANAT_RESULTS_PATH", "results.json"))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Не удалось подготовить файл журнала результатов %s", target, exc_info=True)
        return False

    try:
        records = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
        if not isinstance(records, list):
            records = []
    except json.JSONDecodeError:
        records = []
    except OSError:
        logger.warning("Не удалось прочитать журнал результатов %s", target, exc_info=True)
        return False
    records.append(payload)

    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix="results-", suffix=".json", dir=target.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=2)
            file.write("\n")
        try:
            os.replace(temporary, target)
        except OSError:
            target.write_text(temporary.read_text(encoding="utf-8"), encoding="utf-8")
        return True
    except OSError:
        logger.warning("Не удалось записать журнал результатов %s", target, exc_info=True)
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

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
