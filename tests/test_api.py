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
