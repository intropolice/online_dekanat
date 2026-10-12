from pathlib import Path

from online_dekanat.pipeline import DeanOfficePipeline


def test_pipeline_answers_success_for_complete_study_certificate() -> None:
    result = DeanOfficePipeline(ocr_name="text").process(
        Path("samples/study_certificate.txt"),
        "Хватит ли справки для перевода на 2 курс?",
        doc_type="study_certificate",
    )

    assert result.card.fields["student_name"].value == "Иванов Иван Иванович"
    assert "п. 3.2" in result.answer.text
    assert result.answer.missing_fields == []


def test_pipeline_reports_missing_seal() -> None:
    sample = Path("tests/tmp_missing_seal.txt")
    sample.write_text(
        "\n".join(
            [
                "Справка об обучении",
                "ФИО студента: Иванов Иван Иванович",
                "Название вуза: Южный федеральный университет",
                "Направление подготовки: Прикладная информатика",
                "Форма обучения: очная",
                "Период обучения: 01.09.2024 - 30.06.2025",
                "Справка № 12-34 от 15.06.2025",
                "Печать отсутствует",
            ]
        ),
        encoding="utf-8",
    )
    try:
        result = DeanOfficePipeline(ocr_name="text").process(
            sample,
            "Хватит ли справки для перевода?",
            doc_type="study_certificate",
        )
    finally:
        sample.unlink(missing_ok=True)

    assert "Подпись" in result.answer.missing_fields
    assert "Печать" in result.answer.missing_fields
    assert "Не хватает" in result.answer.text


def test_pipeline_refuses_unknown_topic() -> None:
    result = DeanOfficePipeline(ocr_name="text").process(
        Path("samples/study_certificate.txt"),
        "Какая стоимость обучения?",
        doc_type="study_certificate",
    )

    assert "нет информации" in result.answer.text
