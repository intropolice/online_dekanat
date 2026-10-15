from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from itertools import product
from typing import Any

from online_dekanat.models import NOT_FOUND, DocumentCard, FieldValue

DATE_PATTERN = r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s+\d{1,2}\s+\d{2,4}|\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4})(?!\d)"
OCR_DATE_TOKEN = r"[0-9ОOЗВТIl|]{1,4}"


class DocumentTypeNotDetectedError(ValueError):
    pass


def classify_document(text: str, document_types: dict[str, Any]) -> str | None:
    title_scores: dict[str, float] = {}
    for doc_type, spec in document_types.items():
        title_scores[doc_type] = _label_similarity(spec.get("label", ""), text)
    title_match = _unique_best_match(title_scores)
    if title_match is not None:
        return title_match

    structure_scores = {
        doc_type: sum(
            _label_similarity(signal, text) > 0
            for signal in spec.get("classification_signals", [])
        )
        for doc_type, spec in document_types.items()
    }
    best_score = max(structure_scores.values(), default=0)
    if best_score < 2:
        return None
    return _unique_best_match(
        {
            doc_type: float(score)
            for doc_type, score in structure_scores.items()
            if score >= 2
        }
    )


def _unique_best_match(scores: dict[str, float]) -> str | None:
    nonzero = {doc_type: score for doc_type, score in scores.items() if score > 0}
    if not nonzero:
        return None
    ranked = sorted(nonzero.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.08:
        return None
    return ranked[0][0]


def _label_similarity(label: str, text: str) -> float:
    expected = re.findall(r"[a-zа-яё0-9]+", label.casefold())
    actual = re.findall(r"[a-zа-яё0-9]+", text.casefold())
    if not expected or len(actual) < len(expected):
        return 0.0

    best_score = 0.0
    for start in range(len(actual) - len(expected) + 1):
        similarities = [
            _label_token_similarity(word, target)
            for word, target in zip(actual[start : start + len(expected)], expected)
        ]
        if min(similarities) >= 0.7:
            best_score = max(best_score, sum(similarities) / len(similarities))
    return best_score if best_score >= 0.82 else 0.0


def _label_token_similarity(actual: str, expected: str) -> float:
    if actual == expected:
        return 1.0
    if min(len(actual), len(expected)) <= 2:
        return 0.8 if actual.startswith(expected) or expected.startswith(actual) else 0.0
    return SequenceMatcher(None, actual, expected).ratio()


def extract_card(text: str, document_types: dict[str, Any], doc_type: str | None = None) -> DocumentCard:
    selected_type = doc_type if doc_type is not None else classify_document(text, document_types)
    if selected_type is None:
        raise DocumentTypeNotDetectedError(
            "Не удалось определить тип документа по заголовку. "
            "Поддерживаются справка об обучении, заявление на академический отпуск и справка о практике."
        )
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
    elif kind == "group":
        value = _extract_group(text)
    elif kind == "date_period_part":
        value = _extract_date_period_part(spec.get("part", "start"), text)
    elif kind == "date":
        value = _extract_labeled_date(spec, text)
    elif kind == "labeled_text":
        value = _extract_labeled_text(spec.get("labels", []), text)
    else:
        value = _extract_regex(spec.get("patterns", []), text)
    if key == "issue_date" and value != NOT_FOUND:
        value = _normalize_date(value) or value
    return FieldValue(
        key=key,
        title=spec["title"],
        value=value,
        required=bool(spec.get("required", True)),
    )


def _extract_group(text: str) -> str:
    lines = text.splitlines()
    label = re.compile(
        r"^[ \t]*(?:учебная[ \t]+)?группа(?:[ \t]+и[ \t]+курс)?"
        r"(?:[ \t]*[:：][ \t]*(.*)|[ \t]+(.+))?[ \t]*$",
        flags=re.IGNORECASE,
    )
    group_code = re.compile(r"[a-zа-яё]{2,}[ -]?\d+(?:[-/][a-zа-яё0-9]+)*", flags=re.IGNORECASE)
    for index, line in enumerate(lines):
        match = label.search(line)
        if not match:
            continue
        value = (match.group(1) or match.group(2) or "").strip(" .:;\t\r\n")
        if value:
            return value
        if index + 1 < len(lines):
            next_line = lines[index + 1].strip()
            if group_code.fullmatch(next_line):
                return next_line
    return NOT_FOUND


def _extract_date_period_part(part: str, text: str) -> str:
    lines = text.splitlines()
    explicit = "начало периода обучения" if part == "start" else "окончание периода обучения"
    for index, line in enumerate(lines):
        date_matches = list(re.finditer(DATE_PATTERN, line, flags=re.IGNORECASE))
        prefix = line[: date_matches[0].start()] if date_matches else line
        if not _date_label_matches(prefix, explicit):
            continue
        if date_matches:
            return _normalize_date(date_matches[0 if part == "start" else -1].group(1)) or NOT_FOUND
        labeled_date = _extract_ocr_date(line)
        if labeled_date:
            return labeled_date
        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index < len(lines):
            next_line = lines[next_index].strip(" \t:;,.")
            next_dates = list(re.finditer(DATE_PATTERN, next_line, flags=re.IGNORECASE))
            if len(next_dates) == 1 and next_line == next_dates[0].group(1):
                return _normalize_date(next_dates[0].group(1)) or NOT_FOUND
            next_date = _extract_ocr_date(next_line, exact=True)
            if next_date:
                return next_date
    for line in lines:
        normalized_line = line.casefold()
        if "период обучения" not in normalized_line and "срок обучения" not in normalized_line:
            continue
        dates = re.findall(DATE_PATTERN, line, flags=re.IGNORECASE)
        if len(dates) >= 2:
            return _normalize_date(dates[0 if part == "start" else 1]) or NOT_FOUND
        if dates and re.search(r"\b(?:с|до|по)\b", normalized_line):
            # A one-date line is handled below if the start/end marker is explicit.
            continue
    for line in lines:
        date_matches = list(re.finditer(DATE_PATTERN, line, flags=re.IGNORECASE))
        markers = list(re.finditer(r"\b(с|до|по)\b", line, flags=re.IGNORECASE))
        for date_match in date_matches:
            previous_markers = [marker for marker in markers if marker.start() < date_match.start()]
            if not previous_markers:
                continue
            marker = previous_markers[-1].group(1).casefold()
            if (part == "start" and marker == "с") or (part == "end" and marker in {"до", "по"}):
                return _normalize_date(date_match.group(1)) or NOT_FOUND
    return NOT_FOUND


def _extract_ocr_date(text: str, *, exact: bool = False) -> str | None:
    compact_pattern = r"(?<!\d)(\d{8})(?!\d)"
    compact = re.fullmatch(r"(\d{8})", text) if exact else re.search(compact_pattern, text)
    if compact:
        value = compact.group(1)
        return _normalize_date(f"{value[:2]} {value[2:4]} {value[4:]}")

    separated_pattern = (
        rf"({OCR_DATE_TOKEN})[ \t./-]+({OCR_DATE_TOKEN})[ \t./-]+({OCR_DATE_TOKEN})"
    )
    separated = (
        re.fullmatch(separated_pattern, text, flags=re.IGNORECASE)
        if exact
        else re.search(
            rf"(?<!\w){separated_pattern}(?!\w)",
            text,
            flags=re.IGNORECASE,
        )
    )
    if not separated:
        return None

    possible_dates: set[str] = set()
    for day, month, year in product(
        *(_ocr_digit_variants(token) for token in separated.groups())
    ):
        normalized = _normalize_date(f"{day} {month} {year}")
        if normalized:
            possible_dates.add(normalized)
    return next(iter(possible_dates)) if len(possible_dates) == 1 else None


def _ocr_digit_variants(token: str) -> list[str]:
    substitutions = {
        "О": "0",
        "O": "0",
        "З": "3",
        "В": "38",
        "Т": "17",
        "I": "1",
        "L": "1",
        "T": "17",
        "l": "1",
        "|": "1",
    }
    choices = [substitutions.get(char.upper(), char) for char in token]
    if any(not choice.isdigit() for choice in choices):
        return []
    return ["".join(digits) for digits in product(*choices)]


def _extract_labeled_date(spec: dict[str, Any], text: str) -> str:
    for line in text.splitlines():
        matches = list(re.finditer(DATE_PATTERN, line, flags=re.IGNORECASE))
        if not matches:
            continue
        labels = spec.get("labels", [])
        if any(_date_label_matches(line[: matches[0].start()], label) for label in labels):
            return _normalize_date(matches[0].group(1)) or NOT_FOUND

    return NOT_FOUND


def _extract_labeled_text(labels: list[str], text: str) -> str:
    for line in text.splitlines():
        words = list(re.finditer(r"[a-zа-яё]+", line, flags=re.IGNORECASE))
        if not words or line[: words[0].start()].strip(" \t:：;,."):
            continue

        matches: list[tuple[int, float, int]] = []
        for label in labels:
            expected = re.findall(r"[a-zа-яё]+", label.casefold())
            if not expected or len(words) < len(expected):
                continue
            actual = [word.group().casefold() for word in words[: len(expected)]]
            similarities = [
                SequenceMatcher(None, actual_word, expected_word).ratio()
                for actual_word, expected_word in zip(actual, expected)
            ]
            score = sum(similarities) / len(similarities)
            if min(similarities) >= 0.65 and score >= 0.78:
                matches.append((len(expected), score, len(expected)))

        if not matches:
            continue

        _, _, matched_words = max(matches)
        remainder = line[words[matched_words - 1].end() :]
        value = remainder.lstrip(" \t:：;,.").strip()
        if value and not _is_blank_placeholder(value):
            return value
    return NOT_FOUND


def _date_label_matches(line_prefix: str, label: str) -> bool:
    tokens = re.findall(r"[а-яё]+", line_prefix.casefold())
    expected = re.findall(r"[а-яё]+", label.casefold())
    if not expected or len(tokens) < len(expected):
        return False
    best_score = 0.0
    for start in range(len(tokens) - len(expected) + 1):
        similarities = [
            SequenceMatcher(None, actual, target).ratio()
            for actual, target in zip(tokens[start : start + len(expected)], expected)
        ]
        if min(similarities) >= 0.65:
            best_score = max(best_score, sum(similarities) / len(similarities))
    return best_score >= 0.78


def _normalize_date(value: str) -> str | None:
    words = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", value.casefold())
    if words:
        months = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
        month = months.get(words.group(2))
        if month:
            try:
                return datetime(int(words.group(3)), month, int(words.group(1))).strftime("%d.%m.%Y")
            except ValueError:
                return None
    formats = ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d %m %Y", "%d.%m.%y", "%d/%m/%y", "%d-%m-%y", "%d %m %y", "%Y-%m-%d")
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return None


def _extract_regex(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        for line in text.splitlines():
            match = re.search(pattern, line, flags=re.IGNORECASE | re.MULTILINE)
            if not match:
                continue
            groups = [
                group.strip(" .:;\t\r\n")
                for group in match.groups()
                if group and group.strip(" .:;\t\r\n")
            ]
            if groups:
                value = " от ".join(groups) if len(groups) == 2 else " ".join(groups)
                if not _is_blank_placeholder(value):
                    return value
                continue
            if match.groups():
                continue
            value = match.group(0).strip(" .:;\t\r\n")
            if value and not _is_blank_placeholder(value):
                return value
    return NOT_FOUND


def _is_blank_placeholder(value: str) -> bool:
    return bool(re.fullmatch(r"[\s_\-‐‑‒–—―.]+", value))


def _extract_presence(spec: dict[str, Any], text: str) -> str:
    normalized = " ".join(text.lower().split())
    if any(term.lower() in normalized for term in spec.get("negative_terms", [])):
        return NOT_FOUND
    required_terms = [term.lower() for term in spec.get("required_terms", [])]
    if required_terms and all(term in normalized for term in required_terms):
        return "есть"
    return NOT_FOUND
