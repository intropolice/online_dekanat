from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from online_dekanat.highlight import (
    _regions_for_card,
    _visual_regions,
    apply_visual_presence,
    create_highlights,
)
from online_dekanat.models import NOT_FOUND, DocumentCard, FieldValue
from online_dekanat.ocr.base import OCRRegion
from online_dekanat.quality import normalize_orientation


def _document_with_signature_disclaimer(
    path: Path,
    *,
    signed: bool,
) -> tuple[list[OCRRegion], DocumentCard]:
    image = Image.new("RGB", (700, 180), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)

    draw.text((30, 30), "Signature", fill="black", font=font)
    signature_bbox = draw.textbbox((30, 30), "Signature", font=font)
    staff_position = (signature_bbox[2] + 1, 30)
    draw.text(staff_position, "staff:", fill="black", font=font)
    draw.line((170, 42, 330, 42), fill="#999999", width=1)
    if signed:
        draw.line((180, 36, 205, 28, 220, 45, 250, 31), fill="black", width=2)

    regions = [
        OCRRegion(
            "Подпись",
            tuple(map(float, signature_bbox)),
            0,
        ),
        OCRRegion(
            "сотрудника:",
            tuple(map(float, draw.textbbox(staff_position, "staff:", font=font))),
            0,
        )
    ]

    x = 30
    ocr_words = "Подпись и печать являются учебными макетами".split()
    visible_words = "Signature and seal are educational mockups".split()
    for ocr_word, visible_word in zip(ocr_words, visible_words):
        bbox = draw.textbbox((x, 100), visible_word, font=font)
        draw.text((x, 100), visible_word, fill="black", font=font)
        regions.append(OCRRegion(ocr_word, tuple(map(float, bbox)), 0))
        x = bbox[2] + 5

    image.save(path)
    card = DocumentCard(
        doc_type="study_certificate",
        doc_label="Справка об обучении",
        fields={"signature": FieldValue("signature", "Подпись")},
        raw_text="",
    )
    return regions, card


def test_signature_disclaimer_words_are_not_mistaken_for_handwriting(tmp_path: Path) -> None:
    source = tmp_path / "blank_signature.png"
    regions, card = _document_with_signature_disclaimer(source, signed=False)

    apply_visual_presence(source, regions, card)

    assert card.fields["signature"].value == NOT_FOUND


def test_real_signature_is_detected_next_to_signature_disclaimer(tmp_path: Path) -> None:
    source = tmp_path / "signed.png"
    regions, card = _document_with_signature_disclaimer(source, signed=True)

    apply_visual_presence(source, regions, card)

    assert card.fields["signature"].value == "есть"


def test_highlights_do_not_create_a_separate_signature_detail(tmp_path: Path) -> None:
    source = tmp_path / "signature_detail.png"
    regions, card = _document_with_signature_disclaimer(source, signed=True)

    highlights = create_highlights(source, regions, card, tmp_path / "highlights")

    assert len(highlights) == 1
    assert all("signature-detail" not in path for path in highlights)


def test_signature_highlight_prefers_handwriting_to_local_label_candidate(
    monkeypatch,
) -> None:
    image = Image.new("RGB", (559, 210), "#10212a")
    draw = ImageDraw.Draw(image)
    draw.rectangle((34, 0, 497, 197), fill="white")
    font = ImageFont.load_default(size=10)
    draw.text((70, 45), "Signature staff:", fill="#333333", font=font)
    draw.line(
        [(212, 47), (220, 43), (224, 50), (230, 41), (235, 50), (243, 44), (250, 51), (257, 42), (264, 48), (279, 44)],
        fill="#244c7b",
        width=2,
    )
    regions = [
        OCRRegion("Подпись", (70, 45, 100, 54), 0),
        OCRRegion("сотрудника:", (101, 45, 147, 54), 0),
    ]
    card = DocumentCard(
        doc_type="study_certificate",
        doc_label="Справка об обучении",
        fields={"signature": FieldValue("signature", "Подпись")},
        raw_text="",
    )
    monkeypatch.setattr(
        "online_dekanat.highlight._find_visual_bbox",
        lambda *_args: (101, 45, 147, 54),
    )

    highlighted = _visual_regions([image], regions, card)

    assert len(highlighted) == 1
    assert highlighted[0].bbox[0] >= 200
    assert highlighted[0].bbox[2] <= 300


def test_signature_is_detected_in_screenshot_with_dark_side_bars(tmp_path: Path) -> None:
    source = tmp_path / "signed_screenshot.png"
    image = Image.new("RGB", (559, 210), "#10212a")
    draw = ImageDraw.Draw(image)
    draw.rectangle((34, 0, 497, 197), fill="white")
    font = ImageFont.load_default(size=10)
    draw.text((70, 45), "Signature staff:", fill="#333333", font=font)
    draw.line((210, 53, 300, 53), fill="#cccccc", width=1)
    draw.line(
        [(212, 47), (220, 43), (224, 50), (230, 41), (235, 50), (243, 44), (250, 51), (257, 42), (264, 48), (279, 44)],
        fill="#244c7b",
        width=2,
    )
    draw.ellipse((340, 10, 415, 85), outline="#315a7a", width=2)
    draw.ellipse((346, 16, 409, 79), outline="#315a7a", width=1)
    image.save(source)

    regions = [
        OCRRegion("Подпись", (70, 45, 100, 54), 0),
        OCRRegion("сотрудника:", (101, 45, 147, 54), 0),
    ]
    card = DocumentCard(
        doc_type="study_certificate",
        doc_label="Справка об обучении",
        fields={"signature": FieldValue("signature", "Подпись")},
        raw_text="",
    )

    apply_visual_presence(source, regions, card)

    assert card.fields["signature"].value == "есть"


def test_signature_detection_uses_normalized_page_coordinates(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "rotated_signed.png"
    regions, card = _document_with_signature_disclaimer(source, signed=True)
    with Image.open(source) as image:
        image.rotate(-90, expand=True).save(source)
    monkeypatch.setattr("online_dekanat.quality._load_pytesseract", lambda: object())
    monkeypatch.setattr("online_dekanat.quality._page_rotation", lambda page, ocr: 90)

    oriented = normalize_orientation(source)
    try:
        apply_visual_presence(oriented, regions, card)
    finally:
        if oriented != source:
            oriented.unlink(missing_ok=True)

    assert card.fields["signature"].value == "есть"


def test_colored_signature_is_detected_when_ocr_misses_its_label(tmp_path: Path) -> None:
    source = tmp_path / "signature_without_ocr_label.png"
    image = Image.new("RGB", (420, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.line(
        [(110, 80), (120, 64), (129, 84), (139, 68), (148, 82), (160, 70), (172, 80), (186, 66), (198, 80)],
        fill="#244c7b",
        width=3,
    )
    draw.ellipse((280, 38, 350, 108), outline="#315a7a", width=2)
    image.save(source)
    regions = [
        OCRRegion("Maarece", (35, 75, 70, 85), 0),
        OCRRegion("струна:", (73, 75, 104, 85), 0),
    ]
    card = DocumentCard(
        doc_type="study_certificate",
        doc_label="Справка об обучении",
        fields={
            "signature": FieldValue("signature", "Подпись"),
            "seal": FieldValue("seal", "Печать"),
        },
        raw_text="",
    )

    apply_visual_presence(source, regions, card)

    assert card.fields["signature"].value == "есть"
    assert card.fields["seal"].value == "есть"


def test_colored_stamp_alone_is_not_mistaken_for_signature(tmp_path: Path) -> None:
    source = tmp_path / "stamp_without_signature.png"
    image = Image.new("RGB", (420, 180), "white")
    ImageDraw.Draw(image).ellipse((280, 38, 350, 108), outline="#315a7a", width=2)
    image.save(source)
    regions = [OCRRegion("Maarece струна:", (35, 75, 104, 85), 0)]
    card = DocumentCard(
        doc_type="study_certificate",
        doc_label="Справка об обучении",
        fields={
            "signature": FieldValue("signature", "Подпись"),
            "seal": FieldValue("seal", "Печать"),
        },
        raw_text="",
    )

    apply_visual_presence(source, regions, card)

    assert card.fields["signature"].value == NOT_FOUND
    assert card.fields["seal"].value == "есть"


def test_group_highlight_matches_ocr_latin_cyrillic_confusion() -> None:
    region = OCRRegion("ABT-23-4", (98, 24, 137, 31), 0)
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={"group": FieldValue("group", "Группа", "ИВТ-23-4")},
        raw_text="",
    )

    highlighted = _regions_for_card([region], card)

    assert len(highlighted) == 1
    assert highlighted[0].bbox == region.bbox


def test_group_highlight_does_not_match_different_group_number() -> None:
    region = OCRRegion("ABT-24-5", (98, 24, 137, 31), 0)
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={"group": FieldValue("group", "Группа", "ИВТ-23-4")},
        raw_text="",
    )

    assert _regions_for_card([region], card) == []


def test_group_and_course_highlights_do_not_overlap_on_the_group_suffix() -> None:
    group_region = OCRRegion("Учебная группа: ЖР-23-3", (0, 10, 160, 20), 0)
    course_region = OCRRegion("Курс: 3", (0, 30, 50, 40), 0)
    card = DocumentCard(
        doc_type="academic_leave_application",
        doc_label="Заявление на академический отпуск",
        fields={
            "group": FieldValue("group", "Группа", "ЖР-23-3"),
            "course": FieldValue("course", "Курс", "3"),
        },
        raw_text="",
    )

    highlighted = _regions_for_card([group_region, course_region], card)

    assert len(highlighted) == 2
    group_highlight, course_highlight = highlighted
    assert group_highlight.bbox[0] > group_region.bbox[0]
    assert group_highlight.bbox[2] == group_region.bbox[2]
    assert course_highlight.bbox[0] > course_region.bbox[0]
    assert course_highlight.bbox[2] == course_region.bbox[2]


def test_study_period_end_highlight_matches_a_numeric_ocr_date_region() -> None:
    region = OCRRegion("31.08.2026", (356, 548, 452, 565), 0)
    card = DocumentCard(
        doc_type="study_certificate",
        doc_label="Справка об обучении",
        fields={
            "study_period_end": FieldValue(
                "study_period_end", "Окончание периода обучения", "31.08.2026"
            )
        },
        raw_text="",
    )

    highlighted = _regions_for_card([region], card)

    assert highlighted == [region]


def test_empty_fuzzy_labeled_fields_do_not_highlight_the_label() -> None:
    from online_dekanat.config import load_document_types
    from online_dekanat.extraction import extract_card

    types = load_document_types()
    study_card = extract_card(
        "Направление подготовкИ: ____________________",
        types,
        "study_certificate",
    )
    leave_card = extract_card(
        "Причина отпусka: ____________________",
        types,
        "academic_leave_application",
    )
    regions = [
        OCRRegion("Направление подготовкИ:", (10, 10, 130, 20), 0),
        OCRRegion("Причина отпусka:", (10, 30, 100, 40), 0),
    ]

    assert _regions_for_card(regions, study_card) == []
    assert _regions_for_card(regions, leave_card) == []
