from __future__ import annotations

from typing import Any

from online_dekanat.models import Answer, DocumentCard

UNKNOWN_TOPIC_KEYWORDS = [
    "стоимость",
    "стоимост",
    "оплата",
    "общежит",
    "стипенд",
    "расписание",
    "преподавател",
]


def answer_question(question: str, card: DocumentCard, regulations: list[dict[str, Any]]) -> Answer:
    if _is_unknown_topic(question):
        return Answer(
            text="В документе и в Положении нет информации по этому вопросу. Обратитесь в деканат.",
            sources=["документ", "Положение"],
            confidence="no-answer",
        )

    rule = _select_rule(question, card.doc_type, regulations)
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


def _is_unknown_topic(question: str) -> bool:
    normalized = question.lower()
    return any(keyword in normalized for keyword in UNKNOWN_TOPIC_KEYWORDS)


def _select_rule(question: str, doc_type: str, regulations: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = question.lower()
    candidates = [rule for rule in regulations if rule["doc_type"] == doc_type]
    if not candidates:
        return None
    scored = []
    for rule in candidates:
        score = sum(1 for keyword in rule.get("intent_keywords", []) if keyword.lower() in normalized)
        scored.append((score, rule))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] == 0:
        return None
    return scored[0][1]
