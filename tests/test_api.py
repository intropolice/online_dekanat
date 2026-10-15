from pathlib import Path

from fastapi.testclient import TestClient


def test_process_upload_errors_are_russian_and_size_is_limited(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ONLINE_DEKANAT_DB_PATH", str(tmp_path / "students.db"))
    monkeypatch.setenv("ONLINE_DEKANAT_STORAGE_ROOT", str(tmp_path / "secure"))
    monkeypatch.setenv(
        "ONLINE_DEKANAT_SESSION_SECRET", "test-session-secret-that-is-long-enough"
    )

    from online_dekanat import api

    with TestClient(api.app) as client:
        unsupported = client.post(
            "/process",
            data={"question": "Проверьте документ"},
            files={"file": ("document.docx", b"contents")},
        )
        assert unsupported.status_code == 400
        assert "Формат файла" in unsupported.json()["detail"]
        assert "unsupported" not in unsupported.text.lower()

        oversized = client.post(
            "/process",
            data={"question": "Проверьте документ"},
            files={"file": ("document.pdf", b"x" * (16 * 1024 * 1024 + 1))},
        )
        assert oversized.status_code == 413
        assert "16 МБ" in oversized.json()["detail"]


def test_anonymous_upload_succeeds_when_results_ledger_is_unavailable(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ONLINE_DEKANAT_DB_PATH", str(tmp_path / "students.db"))
    monkeypatch.setenv("ONLINE_DEKANAT_STORAGE_ROOT", str(tmp_path / "secure"))
    monkeypatch.setenv("ONLINE_DEKANAT_RESULTS_PATH", str(tmp_path / "results.json"))
    monkeypatch.setenv(
        "ONLINE_DEKANAT_SESSION_SECRET", "test-session-secret-that-is-long-enough"
    )

    from online_dekanat import api, pipeline

    def skip_results_ledger(payload: dict) -> bool:
        assert payload
        return False

    monkeypatch.setattr(pipeline, "append_result", skip_results_ledger)
    document = Path("samples/study_certificate.txt").read_bytes()

    with TestClient(api.app) as client:
        response = client.post(
            "/process",
            data={"question": "Проверьте документ", "ocr": "text"},
            files={"file": ("document.txt", document, "text/plain")},
        )

    assert response.status_code == 200
    assert response.json()["card"]["doc_type"] == "study_certificate"


def test_process_reports_when_document_type_cannot_be_detected(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ONLINE_DEKANAT_DB_PATH", str(tmp_path / "students.db"))
    monkeypatch.setenv("ONLINE_DEKANAT_STORAGE_ROOT", str(tmp_path / "secure"))
    monkeypatch.setenv(
        "ONLINE_DEKANAT_SESSION_SECRET", "test-session-secret-that-is-long-enough"
    )

    from online_dekanat import api
    from online_dekanat.extraction import DocumentTypeNotDetectedError

    def reject_unknown_type(*_args, **_kwargs):
        raise DocumentTypeNotDetectedError("Не удалось определить тип документа.")

    monkeypatch.setattr(api.DeanOfficePipeline, "process", reject_unknown_type)

    with TestClient(api.app) as client:
        response = client.post(
            "/process",
            data={"question": "Проверьте документ", "ocr": "text"},
            files={
                "file": (
                    "document.txt",
                    "Справка о доходах".encode("utf-8"),
                    "text/plain",
                )
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == "Не удалось определить тип документа."
