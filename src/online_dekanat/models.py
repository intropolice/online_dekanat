from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

NOT_FOUND = "не найдено"


@dataclass(slots=True)
class FieldValue:
    key: str
    title: str
    value: str = NOT_FOUND
    required: bool = True
    source: str = "document"

    @property
    def is_found(self) -> bool:
        return bool(self.value and self.value != NOT_FOUND)


@dataclass(slots=True)
class DocumentCard:
    doc_type: str
    doc_label: str
    fields: dict[str, FieldValue]
    raw_text: str

    def missing_required(self, required_keys: list[str] | None = None) -> list[FieldValue]:
        keys = required_keys or list(self.fields)
        return [
            self.fields[key]
            for key in keys
            if key in self.fields and self.fields[key].required and not self.fields[key].is_found
        ]


@dataclass(slots=True)
class Answer:
    text: str
    sources: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    confidence: str = "rule-based"


@dataclass(slots=True)
class PipelineResult:
    card: DocumentCard
    answer: Answer

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
