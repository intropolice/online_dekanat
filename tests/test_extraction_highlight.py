from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from online_dekanat.answering import answer_question
from online_dekanat.config import load_regulations
from online_dekanat.extraction import extract_card
from online_dekanat.highlight import _regions_for_card, _visual_regions, apply_visual_presence
from online_dekanat.models import NOT_FOUND, DocumentCard, FieldValue
from online_dekanat.ocr.base import OCRRegion


def test_highlights_group_and_course_values_separately() -> None:
    regions = [
        OCRRegion("Учебная группа: ХР-23-3", (10, 100, 260, 120)),
        OCRRegion("Курс: 3", (10, 130, 90, 150)),
    ]
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={
            "group": FieldValue(key="group", title="Группа", value="ХР-23-3"),
            "course": FieldValue(key="course", title="Курс", value="3"),
        },
        raw_text="",
    )

    highlights = _regions_for_card(regions, card)

    assert len(highlights) == 2
    group_highlight = next(region for region in highlights if region.bbox[1] == 100)
    course_highlight = next(region for region in highlights if region.bbox[1] == 130)
    assert group_highlight.bbox[0] > regions[0].bbox[0]
    assert group_highlight.bbox[2] - group_highlight.bbox[0] > 50
    assert course_highlight.text == "Курс: 3"


def test_extracts_group_and_course_separately() -> None:
    document_types = json.loads(Path("config/document_types.json").read_text(encoding="utf-8"))
    text = "\n".join(
        [
            "ФИО студента: Захаров Роман Дмитриевич",
            "Учебная группа: ИВТ-22-3",
            "Курс: 3",
            "Причина отпуска: по семейным обстоятельствам",
        ]
    )

    card = extract_card(text, document_types, "academic_leave_application")

    assert card.fields["group"].title == "Группа"
    assert card.fields["group"].value == "ИВТ-22-3"
    assert card.fields["course"].title == "Курс"
    assert card.fields["course"].value == "3"

    card_without_course = extract_card(text.replace("Курс: 3\n", ""), document_types, "academic_leave_application")
    answer = answer_question("Можно оформить академический отпуск?", card_without_course, load_regulations())

    assert card_without_course.fields["course"].value == NOT_FOUND
    assert "Курс" in answer.missing_fields


def test_extracts_practice_supervisor_and_separate_practice_dates() -> None:
    document_types = json.loads(Path("config/document_types.json").read_text(encoding="utf-8"))
    text = "\n".join(
        [
            "Справка о практике",
            "Дата начала практики: 1 июня 2026 года",
            "Дата окончания практики: 15 июля 2026 года",
            "ФИО руководителя практики: Кузнецова Мария Сергеевна",
        ]
    )

    card = extract_card(text, document_types, "practice_certificate")

    assert card.fields["practice_start"].title == "Дата начала практики"
    assert card.fields["practice_start"].value == "01.06.2026"
    assert card.fields["practice_end"].title == "Дата окончания практики"
    assert card.fields["practice_end"].value == "15.07.2026"
    assert card.fields["supervisor"].value == "Кузнецова Мария Сергеевна"

    answer = answer_question("Проверьте справку о практике", card, load_regulations())
    assert "Руководитель практики" not in answer.missing_fields
    assert "Дата начала практики" not in answer.missing_fields
    assert "Дата окончания практики" not in answer.missing_fields


def test_does_not_extract_organization_label_as_student_name() -> None:
    document_types = json.loads(Path("config/document_types.json").read_text(encoding="utf-8"))
    text = "\n".join(
        [
            "СПРАВКА О ПРАКТИКЕ",
            "ФИО студента:",
            "Название организации: Учебная организация «Орбита»",
            "Дата начала практики: 01.06.2026",
            "Дата окончания практики: 15.07.2026",
        ]
    )

    card = extract_card(text, document_types, "practice_certificate")

    assert card.fields["student_name"].value == NOT_FOUND
    assert card.fields["organization"].value == "Учебная организация «Орбита»"


def test_extracts_application_date_when_ocr_misreads_its_label() -> None:
    document_types = json.loads(Path("config/document_types.json").read_text(encoding="utf-8"))
    text = "\n".join(
        [
            "Дата начала отпуска: 1 ноября 2026 года",
            "Дата окончания отпуска: 31 августа 2027 года",
            "Дата заяапения: 7 октября 2026 года",
        ]
    )

    card = extract_card(text, document_types, "academic_leave_application")

    assert card.fields["leave_start"].value == "01.11.2026"
    assert card.fields["leave_end"].value == "31.08.2027"
    assert card.fields["application_date"].value == "07.10.2026"


def test_highlights_practice_supervisor_and_separate_dates() -> None:
    regions = [
        OCRRegion("ФИО руководителя практики: Кузнецова Мария Сергеевна", (20, 120, 430, 140)),
        OCRRegion("Дата начала практики: 1 июня 2026 года", (20, 150, 340, 170)),
        OCRRegion("Дата окончания практики: 15 июля 2026 года", (20, 180, 360, 200)),
    ]
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={
            "supervisor": FieldValue(key="supervisor", title="Руководитель практики", value="Кузнецова Мария Сергеевна"),
            "practice_start": FieldValue(key="practice_start", title="Дата начала практики", value="01.06.2026"),
            "practice_end": FieldValue(key="practice_end", title="Дата окончания практики", value="15.07.2026"),
        },
        raw_text="",
    )

    highlights = _regions_for_card(regions, card)

    assert len(highlights) == 3
    supervisor_highlight = next(region for region in highlights if region.bbox[1] == 120)
    assert supervisor_highlight.bbox[0] > regions[0].bbox[0]
    assert supervisor_highlight.bbox[2] == regions[0].bbox[2]


def test_signature_highlight_uses_signature_not_plural_footer_label() -> None:
    page = Image.new("RGB", (500, 700), "white")
    draw = ImageDraw.Draw(page)
    draw.line((205, 302, 215, 295, 221, 310, 229, 291, 235, 306, 243, 298, 251, 304), fill="blue", width=3)
    draw.text((185, 600), "являются учебными макетами", fill="black")
    regions = [
        OCRRegion("Подпись студента:", (60, 295, 155, 315)),
        OCRRegion("Подпись и печать", (60, 600, 180, 618)),
        OCRRegion("являются учебными макетами", (180, 600, 370, 618)),
    ]
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={"signature": FieldValue(key="signature", title="Подпись студента", value="есть")},
        raw_text="",
    )

    visual_regions = _visual_regions([page], regions, card)

    assert len(visual_regions) == 1
    assert visual_regions[0].bbox[1] < 350
    assert visual_regions[0].bbox[0] > 150


def test_signature_highlight_tightly_frames_handwriting() -> None:
    page = Image.new("RGB", (451, 75), "white")
    draw = ImageDraw.Draw(page)
    draw.line((213, 34, 337, 34), fill=(215, 215, 215), width=1)
    draw.line(
        [(221, 31), (229, 26), (233, 35), (239, 23), (246, 33), (253, 21), (260, 32), (267, 25), (274, 34), (281, 23), (288, 33), (295, 24), (303, 32), (311, 27)],
        fill=(35, 66, 116),
        width=2,
    )
    regions = [OCRRegion("Подпись студента:", (32, 27, 122, 41))]
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={"signature": FieldValue(key="signature", title="Подпись студента", value="есть")},
        raw_text="",
    )

    visual_regions = _visual_regions([page], regions, card)

    assert len(visual_regions) == 1
    x1, y1, x2, y2 = visual_regions[0].bbox
    assert 215 <= x1 <= 222
    assert 20 <= y1 <= 25
    assert 307 <= x2 <= 317
    assert 33 <= y2 <= 40


def test_detects_light_signature_and_marks_card_field_present(tmp_path: Path) -> None:
    page = Image.new("RGB", (451, 75), "white")
    draw = ImageDraw.Draw(page)
    draw.line((213, 34, 337, 34), fill=(215, 215, 215), width=1)
    draw.line(
        [(221, 31), (229, 26), (233, 35), (239, 23), (246, 33), (253, 21), (260, 32), (267, 25), (274, 34), (281, 23), (288, 33), (295, 24), (303, 32), (311, 27)],
        fill=(165, 180, 210),
        width=2,
    )
    source = tmp_path / "light_signature.png"
    page.save(source)
    regions = [OCRRegion("Подпись студента:", (32, 27, 122, 41))]
    signature_field = FieldValue(key="signature", title="Подпись студента", value=NOT_FOUND)
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={"signature": signature_field},
        raw_text="",
    )

    apply_visual_presence(source, regions, card)

    assert signature_field.value == "есть"


def test_highlights_signature_above_and_overlapping_long_label(tmp_path: Path) -> None:
    page = Image.new("RGB", (384, 128), "white")
    draw = ImageDraw.Draw(page)
    draw.line((230, 53, 347, 53), fill=(215, 215, 215), width=1)
    draw.line(
        [(231, 50), (240, 43), (244, 52), (250, 39), (257, 50), (265, 42), (272, 49), (280, 38), (287, 51), (295, 41), (302, 48), (311, 40), (318, 49)],
        fill=(35, 66, 116),
        width=2,
    )
    regions = [OCRRegion("Подпись сотрудника:", (264, 47, 365, 57))]
    signature_field = FieldValue(key="signature", title="Подпись", value=NOT_FOUND)
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={"signature": signature_field},
        raw_text="",
    )
    source = tmp_path / "signature_overlapping_label.png"
    page.save(source)

    highlights = _visual_regions([page], regions, card, include_empty_signature_slot=True)
    apply_visual_presence(source, regions, card)

    assert len(highlights) == 1
    x1, y1, x2, y2 = highlights[0].bbox
    assert x1 <= 232
    assert x2 >= 317
    assert y1 <= 39 and y2 <= 55
    assert signature_field.value == "есть"


def test_nearby_seal_does_not_count_as_signature(tmp_path: Path) -> None:
    page = Image.new("RGB", (605, 120), "white")
    draw = ImageDraw.Draw(page)
    draw.line((235, 42, 342, 42), fill=(205, 205, 205), width=1)
    draw.ellipse((400, 8, 468, 76), outline=(65, 105, 155), width=3)
    draw.text((418, 30), "М.П.", fill=(65, 105, 155))
    source = tmp_path / "blank_signature_with_seal.png"
    page.save(source)
    regions = [OCRRegion("Подпись руководителя:", (110, 20, 240, 55))]
    signature_field = FieldValue(key="signature", title="Подпись", value="есть")
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={"signature": signature_field},
        raw_text="Подпись руководителя:",
    )

    visual_regions = _visual_regions([page], regions, card)
    apply_visual_presence(source, regions, card)

    assert visual_regions == []
    assert signature_field.value == NOT_FOUND


def test_real_signature_is_detected_without_counting_adjacent_seal(tmp_path: Path) -> None:
    page = Image.new("RGB", (605, 120), "white")
    draw = ImageDraw.Draw(page)
    draw.line((235, 42, 342, 42), fill=(205, 205, 205), width=1)
    draw.line(
        [(241, 41), (249, 30), (255, 42), (263, 27), (270, 39), (279, 31), (286, 42), (294, 28), (303, 40), (311, 32), (320, 41), (329, 30), (337, 38)],
        fill=(35, 66, 116),
        width=2,
    )
    draw.ellipse((400, 8, 468, 76), outline=(65, 105, 155), width=3)
    draw.text((418, 30), "М.П.", fill=(65, 105, 155))
    source = tmp_path / "signature_with_adjacent_seal.png"
    page.save(source)
    regions = [OCRRegion("Подпись руководителя:", (110, 20, 240, 55))]
    signature_field = FieldValue(key="signature", title="Подпись", value=NOT_FOUND)
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={"signature": signature_field},
        raw_text="",
    )

    visual_regions = _visual_regions([page], regions, card)
    apply_visual_presence(source, regions, card)

    assert len(visual_regions) == 1
    assert visual_regions[0].bbox[2] < 400
    assert signature_field.value == "есть"


def test_recognizes_signature_when_ocr_misses_the_signature_word(tmp_path: Path) -> None:
    page = Image.new("RGB", (347, 117), "white")
    draw = ImageDraw.Draw(page)
    draw.line((207, 41, 331, 41), fill=(215, 215, 215), width=1)
    draw.line(
        [(215, 39), (223, 31), (228, 41), (235, 28), (242, 39), (250, 30), (258, 40), (266, 27), (274, 40), (282, 31), (290, 39), (299, 29), (307, 37)],
        fill=(35, 66, 116),
        width=2,
    )
    source = tmp_path / "ocr_missed_signature_label.png"
    page.save(source)
    regions = [
        OCRRegion("Tonruce", (22, 22, 65, 54)),
        OCRRegion("сотруднина:", (72, 22, 127, 54)),
        OCRRegion("Арк", (214, 24, 305, 44)),
    ]
    signature_field = FieldValue(key="signature", title="Подпись", value=NOT_FOUND)
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={"signature": signature_field},
        raw_text="",
    )

    visual_regions = _visual_regions([page], regions, card)
    apply_visual_presence(source, regions, card)

    assert len(visual_regions) == 1
    assert visual_regions[0].bbox[0] <= 216
    assert visual_regions[0].bbox[2] >= 305
    assert signature_field.value == "есть"


def test_empty_signature_slot_is_highlighted_to_the_right_without_marking_signature_present(tmp_path: Path) -> None:
    page = Image.new("RGB", (544, 100), "white")
    draw = ImageDraw.Draw(page)
    draw.line((216, 52, 308, 52), fill=(215, 215, 215), width=1)
    regions = [OCRRegion("Подпись руководителя:", (80, 47, 164, 57))]
    signature_field = FieldValue(key="signature", title="Подпись", value=NOT_FOUND)
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={"signature": signature_field},
        raw_text="",
    )

    highlight_regions = _visual_regions([page], regions, card, include_empty_signature_slot=True)
    source = tmp_path / "practice.png"
    page.save(source)
    apply_visual_presence(source, regions, card)

    assert len(highlight_regions) == 1
    x1, y1, x2, y2 = highlight_regions[0].bbox
    assert x1 > regions[0].bbox[2]
    assert x1 <= 216 and x2 >= 308
    assert 48 <= y1 <= 52 and 52 <= y2 <= 56
    assert signature_field.value == NOT_FOUND


def test_signature_target_starts_after_split_multiword_label(tmp_path: Path) -> None:
    page = Image.new("RGB", (500, 100), "white")
    draw = ImageDraw.Draw(page)
    draw.line((237, 53, 361, 53), fill=(215, 215, 215), width=1)
    draw.line(
        [(100, 54), (105, 49), (110, 56), (115, 50), (120, 56), (125, 49), (130, 55), (135, 50), (140, 56), (145, 49), (150, 55), (155, 50), (160, 56), (165, 51)],
        fill=(45, 45, 45),
        width=1,
    )
    regions = [
        OCRRegion("Подпись", (56, 48, 95, 57)),
        OCRRegion("руководителя:", (98, 48, 168, 57)),
    ]
    signature_field = FieldValue(key="signature", title="Подпись", value=NOT_FOUND)
    card = DocumentCard(
        doc_type="practice_certificate",
        doc_label="Справка о практике",
        fields={"signature": signature_field},
        raw_text="",
    )

    highlights = _visual_regions([page], regions, card, include_empty_signature_slot=True)
    source = tmp_path / "practice.png"
    page.save(source)
    apply_visual_presence(source, regions, card)

    assert len(highlights) == 1
    x1, y1, x2, y2 = highlights[0].bbox
    assert x1 >= 230
    assert x2 >= 360
    assert y1 <= 53 <= y2
    assert signature_field.value == NOT_FOUND
