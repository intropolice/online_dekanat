from __future__ import annotations

from typing import Any

from online_dekanat.models import Answer, DocumentCard


def answer_question(question: str, card: DocumentCard, regulations: list[dict[str, Any]]) -> Answer:
    rule = _select_rule(card.doc_type, regulations)
    if rule is None:
        return Answer(
            text="В документе и в Положении нет достаточных данных для ответа. Обратитесь в деканат.",
            sources=["документ", "Положение"],
            confidence="no-answer",
        )

    missing = card.missing_required(rule["required_fields"])
    source = f"п. {rule['id']} Положения"
    if missing:
        missing_titles = [field.title for field in missing]
        text = rule["missing"].format(missing=", ".join(missing_titles))
        return Answer(
            text=text,
            sources=[source, *[f"поле: {title}" for title in missing_titles]],
            missing_fields=missing_titles,
        )
    return Answer(text=rule["success"], sources=[source, *[f"поле: {field.title}" for field in card.fields.values()]])


def _select_rule(doc_type: str, regulations: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [rule for rule in regulations if rule["doc_type"] == doc_type]
    if len(matches) != 1:
        return None
    return matches[0]
