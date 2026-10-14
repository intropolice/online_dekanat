from online_dekanat.config import load_document_types
from online_dekanat.extraction import extract_card
from online_dekanat.models import NOT_FOUND


def test_academic_leave_extracts_written_dates_and_separate_group_and_course() -> None:
    text = "\n".join(
        [
            "Прошу предоставить академический отпуск на указанный период.",
            "ФИО студента:",
            "Учебная группа: ИВТ-23-4",
            "Курс: 4",
            "Причина отпуска: в связи с уходом за родственником",
            "Дата начала отпуска: 1 ноября 2026 года",
            "Дата окончания отпуска: 31 августа 2027 года",
            "Дата заяпления: 6 октября 2026 года",
        ]
    )

    card = extract_card(text, load_document_types(), "academic_leave_application")

    assert card.fields["student_name"].value == NOT_FOUND
    assert card.fields["group"].value == "ИВТ-23-4"
    assert card.fields["course"].value == "4"
    assert card.fields["leave_start"].value == "01.11.2026"
    assert card.fields["leave_end"].value == "31.08.2027"
    assert card.fields["application_date"].value == "06.10.2026"
    assert "group_course" not in card.fields


def test_student_name_does_not_cross_line_into_group_label() -> None:
    text = "\n".join(
        [
            "ФИО студента:",
            "Учебная группа: ИВТ-23-4",
            "Курс: 4",
        ]
    )

    card = extract_card(text, load_document_types(), "academic_leave_application")

    assert card.fields["student_name"].value == NOT_FOUND


def test_student_name_is_extracted_when_present_on_its_label_line() -> None:
    text = "ФИО студента: Иванов Иван Иванович\nУчебная группа: ИВТ-23-4"

    card = extract_card(text, load_document_types(), "academic_leave_application")

    assert card.fields["student_name"].value == "Иванов Иван Иванович"


def test_group_is_extracted_when_ocr_places_its_value_on_the_next_line() -> None:
    text = "Группа и курс\nИВТ-23-4\nКурс: 4"

    card = extract_card(text, load_document_types(), "academic_leave_application")

    assert card.fields["group"].value == "ИВТ-23-4"
    assert card.fields["course"].value == "4"


def test_empty_group_does_not_capture_the_next_course_label() -> None:
    text = "Группа:\nКурс: 4"

    card = extract_card(text, load_document_types(), "academic_leave_application")

    assert card.fields["group"].value == NOT_FOUND
    assert card.fields["course"].value == "4"


def test_practice_certificate_extracts_dates_supervisor_and_certificate_number() -> None:
    text = "\n".join(
        [
            "ФИО студента: Шестаков Олег Алексеевич",
            "Название организации: Учебная организация «Спектр»",
            "Дата начала практики: 1 июня 2026 года",
            "Дата окончания пражтики: 15 июля 2026 года",
            "ФИО руководителя практики: Ильина Елена Андреевна",
            "Оценка: зачтено",
            "Номер справки: ПР-2024",
            "Дата выдачи: 17 июня 2026 года",
        ]
    )

    card = extract_card(text, load_document_types(), "practice_certificate")

    assert card.fields["practice_start"].value == "01.06.2026"
    assert card.fields["practice_end"].value == "15.07.2026"
    assert card.fields["supervisor"].value == "Ильина Елена Андреевна"
    assert card.fields["issue_number"].title == "Номер справки"
    assert card.fields["issue_number"].value == "ПР-2024"
    assert card.fields["issue_date"].value == "17.06.2026"
    assert "practice_period" not in card.fields
