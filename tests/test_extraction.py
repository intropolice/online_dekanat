from online_dekanat.config import load_document_types
from online_dekanat.extraction import classify_document, extract_card
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


def test_practice_end_is_not_guessed_from_issue_date_when_label_has_no_date() -> None:
    text = "\n".join(
        [
            "Дата начала практики: 01.06.2026",
            "Дата окончания практики:",
            "Дата выдачи: 23.07.2026",
        ]
    )

    card = extract_card(text, load_document_types(), "practice_certificate")

    assert card.fields["practice_end"].value == NOT_FOUND
    assert card.fields["issue_date"].value == "23.07.2026"


def test_empty_practice_fields_do_not_capture_values_from_the_next_line() -> None:
    text = "\n".join(
        [
            "Организация:",
            "Дата начала практики: 01.06.2026",
            "ФИО руководителя практики: ",
            "Оценка: удовлетворительно",
        ]
    )

    card = extract_card(text, load_document_types(), "practice_certificate")

    assert card.fields["organization"].value == NOT_FOUND
    assert card.fields["supervisor"].value == NOT_FOUND
    assert card.fields["grade"].value == "удовлетворительно"


def test_blank_form_lines_are_not_extracted_as_field_values() -> None:
    text = "\n".join(
        [
            "Направление подготовки: ______________________________",
            "Причина отпуска: ______________________________",
        ]
    )

    study_card = extract_card(text, load_document_types(), "study_certificate")
    leave_card = extract_card(text, load_document_types(), "academic_leave_application")

    assert study_card.fields["program"].value == NOT_FOUND
    assert leave_card.fields["leave_reason"].value == NOT_FOUND


def test_fuzzy_field_labels_do_not_become_values_or_highlight_candidates() -> None:
    types = load_document_types()
    study_text = "Направление подготовкИ: ____________________"
    leave_text = "Причина отпусka: ____________________"

    study_card = extract_card(study_text, types, "study_certificate")
    leave_card = extract_card(leave_text, types, "academic_leave_application")

    assert study_card.fields["program"].value == NOT_FOUND
    assert leave_card.fields["leave_reason"].value == NOT_FOUND


def test_labeled_text_fields_extract_values_after_complete_labels() -> None:
    types = load_document_types()
    study_card = extract_card(
        "Направление подготовкИ: 09.03.01 Информатика и вычислительная техника",
        types,
        "study_certificate",
    )
    leave_card = extract_card(
        "Причина отпусka: по семейным обстоятельствам",
        types,
        "academic_leave_application",
    )

    assert study_card.fields["program"].value == "09.03.01 Информатика и вычислительная техника"
    assert leave_card.fields["leave_reason"].value == "по семейным обстоятельствам"


def test_leave_dates_are_not_guessed_from_other_labeled_dates() -> None:
    missing_start = extract_card(
        "\n".join(
            [
                "Дата начала отпуска: ____________________",
                "Дата окончания отпуска: 31 августа 2027 года",
                "Дата заявления: 6 октября 2026 года",
            ]
        ),
        load_document_types(),
        "academic_leave_application",
    )
    missing_end = extract_card(
        "\n".join(
            [
                "Дата начала отпуска: 1 ноября 2026 года",
                "Дата окончания отпуска: ____________________",
                "Дата заявления: 2 октября 2026 года",
            ]
        ),
        load_document_types(),
        "academic_leave_application",
    )

    assert missing_start.fields["leave_start"].value == NOT_FOUND
    assert missing_start.fields["leave_end"].value == "31.08.2027"
    assert missing_end.fields["leave_start"].value == "01.11.2026"
    assert missing_end.fields["leave_end"].value == NOT_FOUND


def test_practice_start_is_not_guessed_from_end_or_issue_date() -> None:
    card = extract_card(
        "\n".join(
            [
                "Дата начала практики: ____________________",
                "Дата окончания практики: 15 июля 2026 года",
                "Дата выдачи: 23 июля 2026 года",
            ]
        ),
        load_document_types(),
        "practice_certificate",
    )

    assert card.fields["practice_start"].value == NOT_FOUND
    assert card.fields["practice_end"].value == "15.07.2026"
    assert card.fields["issue_date"].value == "23.07.2026"


def test_document_type_is_detected_despite_an_ocr_typo_in_the_title() -> None:
    types = load_document_types()
    examples = [
        ("СПРАВКА ОБ ОБУЧЕННИ", "study_certificate"),
        ("СПРАВКА О ПРАЖТИКЕ", "practice_certificate"),
        ("ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУС", "academic_leave_application"),
    ]

    for text, expected_type in examples:
        assert classify_document(text, types) == expected_type
        assert extract_card(text, types).doc_type == expected_type


def test_document_type_is_detected_from_the_titles_shown_in_the_images() -> None:
    types = load_document_types()
    examples = [
        ("СПРАВКА ОБ ОБУЧЕНИИ", "study_certificate"),
        ("ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК", "academic_leave_application"),
        ("СПРАВКА О ПРАКТИКЕ", "practice_certificate"),
    ]

    for title, expected_type in examples:
        assert classify_document(title, types) == expected_type


def test_document_type_title_can_be_split_across_ocr_lines() -> None:
    types = load_document_types()

    assert classify_document("ЗАЯВЛЕНИЕ НА\nАКАДЕМИЧЕСКИЙ ОТПУСК", types) == (
        "academic_leave_application"
    )


def test_document_type_uses_distinctive_form_fields_when_ocr_misses_the_title() -> None:
    types = load_document_types()
    examples = [
        (
            "Направление подготовки:\nФорма обучения:\n"
            "Начало периода обучения:\nОкончание периода обучения:",
            "study_certificate",
        ),
        (
            "Учебная группа:\nПричина отпуска:\nДата начала отпуска:\n"
            "Дата окончания отпуска:",
            "academic_leave_application",
        ),
        (
            "Организация:\nДата начала практики:\nДата окончания практики:\n"
            "Руководитель практики:",
            "practice_certificate",
        ),
    ]

    for text, expected_type in examples:
        assert classify_document(text, types) == expected_type


def test_unrecognized_document_is_not_silently_classified_as_a_study_certificate() -> None:
    types = load_document_types()

    assert classify_document("Справка о доходах", types) is None

    try:
        extract_card("Справка о доходах", types)
    except ValueError as exc:
        assert "Не удалось определить тип документа" in str(exc)
    else:
        raise AssertionError("Unrecognized document was assigned a document type")


def test_pipeline_document_type_is_independent_of_the_question(
    monkeypatch, tmp_path
) -> None:
    from contextlib import contextmanager

    from online_dekanat import pipeline as pipeline_module
    from online_dekanat.models import Answer
    from online_dekanat.ocr.base import OCRResult
    from online_dekanat.pipeline import DeanOfficePipeline

    document_text = "\n".join(
        [
            "СПРАВКА ОБ ОБУЧЕНИИ",
            "Направление подготовки: 09.03.01 Информатика",
            "Начало периода обучения: 01.09.2024",
        ]
    )
    source = tmp_path / "document.txt"
    source.write_text(document_text, encoding="utf-8")

    class Storage:
        @contextmanager
        def decrypted_temporary_file(self, path):
            yield path

    class OCR:
        def recognize(self, _path):
            return OCRResult(text=document_text, engine="test")

    class Database:
        def save_result(self, _result):
            return "test-result"

    monkeypatch.setattr(pipeline_module, "assess_document", lambda _path: {})
    monkeypatch.setattr(pipeline_module, "normalize_orientation", lambda path: path)
    monkeypatch.setattr(pipeline_module, "prepare_for_ocr", lambda path, _quality: path)
    monkeypatch.setattr(pipeline_module, "rescale_ocr_regions", lambda regions, _src, _dst: regions)
    monkeypatch.setattr(pipeline_module, "apply_visual_presence", lambda _path, _regions, _card: None)
    monkeypatch.setattr(pipeline_module, "create_highlights", lambda _path, _regions, _card: [])
    monkeypatch.setattr(
        pipeline_module,
        "answer_question",
        lambda question, _card, _regulations: Answer(text=question),
    )

    pipeline = DeanOfficePipeline.__new__(DeanOfficePipeline)
    pipeline.document_types = load_document_types()
    pipeline.regulations = []
    pipeline.ocr_client = OCR()
    pipeline.storage = Storage()
    pipeline.database = Database()

    result_one = pipeline.process(source, question="Какая дата выдачи?")
    result_two = pipeline.process(source, question="Сколько стоит общежитие?")

    assert result_one.card.doc_type == result_two.card.doc_type == "study_certificate"
    assert result_one.answer.text != result_two.answer.text


def test_study_period_end_is_extracted_when_ocr_splits_label_and_date() -> None:
    text = "\n".join(
        [
            "Окончание периода обучения:",
            "31.08.2026",
            "Дата выдачи: 01.09.2026",
        ]
    )

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_end"].value == "31.08.2026"
    assert card.fields["issue_date"].value == "01.09.2026"


def test_study_period_end_tolerates_ocr_typo_in_label() -> None:
    text = "Окончание периода обучени: 31.08.2026"

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_end"].value == "31.08.2026"


def test_study_period_end_extracts_date_with_spaces_from_ocr() -> None:
    text = "Отончание периода обучения 31 08 2026"

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_end"].value == "31.08.2026"


def test_study_period_start_extracts_compact_date_from_ocr() -> None:
    text = "Начало периода обучения: 01092023"

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_start"].value == "01.09.2023"


def test_study_period_end_corrects_unambiguous_ocr_digit_confusions() -> None:
    text = "Окончание периода обучения: ВТ 08 2028"

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_end"].value == "31.08.2028"


def test_study_period_does_not_guess_ambiguous_ocr_digits() -> None:
    text = "Окончание периода обучения: В 08 2028"

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_end"].value == NOT_FOUND


def test_study_period_end_does_not_use_the_next_labeled_date() -> None:
    text = "\n".join(
        [
            "Окончание периода обучения:",
            "Дата выдачи: 01.09.2026",
        ]
    )

    card = extract_card(text, load_document_types(), "study_certificate")

    assert card.fields["study_period_end"].value == NOT_FOUND
