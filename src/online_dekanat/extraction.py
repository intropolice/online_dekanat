from __future__ import annotations

import re
from typing import Any

from online_dekanat.models import NOT_FOUND, DocumentCard, FieldValue


def classify_document(text: str, document_types: dict[str, Any]) -> str:
    normalized = text.lower()
    scores: dict[str, int] = {}
    for doc_type, spec in document_types.items():
        scores[doc_type] = sum(1 for keyword in spec.get("keywords", []) if keyword.lower() in normalized)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "study_certificate"
    return best


def extract_card(text: str, document_types: dict[str, Any], doc_type: str | None = None) -> DocumentCard:
    selected_type = doc_type or classify_document(text, document_types)
    if selected_type not in document_types:
        raise ValueError(f"Unknown document type: {selected_type}")
    spec = document_types[selected_type]
    fields = {
        key: _extract_field(key, field_spec, text)
        for key, field_spec in spec["fields"].items()
    }
    return DocumentCard(
        doc_type=selected_type,
        doc_label=spec["label"],
        fields=fields,
        raw_text=text,
    )


def _extract_field(key: str, spec: dict[str, Any], text: str) -> FieldValue:
    kind = spec.get("kind", "regex")
    if kind == "presence":
        value = _extract_presence(spec, text)
    else:
        value = _extract_regex(spec.get("patterns", []), text)
    return FieldValue(
        key=key,
        title=spec["title"],
        value=value,
        required=bool(spec.get("required", True)),
    )


def _extract_regex(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            groups = [group.strip(" .:;\t\r\n") for group in match.groups() if group]
            if groups:
                return " от ".join(groups) if len(groups) == 2 else " ".join(groups)
            return match.group(0).strip()
    return NOT_FOUND


def _extract_presence(spec: dict[str, Any], text: str) -> str:
    normalized = " ".join(text.lower().split())
    if any(term.lower() in normalized for term in spec.get("negative_terms", [])):
        return NOT_FOUND
    required_terms = [term.lower() for term in spec.get("required_terms", [])]
    if required_terms and all(term in normalized for term in required_terms):
        return "есть"
    return NOT_FOUND
