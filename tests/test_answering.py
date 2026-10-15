from online_dekanat.answering import answer_question
from online_dekanat.config import load_document_types, load_regulations
from online_dekanat.extraction import extract_card


def test_document_rule_and_result_are_independent_of_question_wording() -> None:
    types = load_document_types()
    regulations = load_regulations()
    samples = {
        "study_certificate": "СПРАВКА ОБ ОБУЧЕНИИ",
        "academic_leave_application": "ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК",
        "practice_certificate": "СПРАВКА О ПРАКТИКЕ",
    }

    for expected_type, text in samples.items():
        card = extract_card(text, types)
        answers = [
            answer_question(question, card, regulations)
            for question in (
                "Проверьте документ",
                "Можно ли перевестись?",
                "Сколько стоит общежитие?",
            )
        ]

        assert card.doc_type == expected_type
        assert len({answer.text for answer in answers}) == 1
        assert len({tuple(answer.sources) for answer in answers}) == 1
        assert answers[0].confidence != "no-answer"


def test_rule_selection_requires_one_regulation_for_detected_type() -> None:
    types = load_document_types()
    card = extract_card("СПРАВКА О ПРАКТИКЕ", types)
    regulations = load_regulations()

    assert answer_question("Проверьте", card, regulations).confidence != "no-answer"
    assert answer_question("Проверьте", card, regulations + regulations[-1:]).confidence == "no-answer"
