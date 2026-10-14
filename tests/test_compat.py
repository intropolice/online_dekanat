import builtins
import sys
import tomllib
from pathlib import Path

import pytest

from online_dekanat.database import StudentDatabase
from online_dekanat.config import load_document_types
from online_dekanat.extraction import extract_card
from online_dekanat.models import Answer, PipelineResult
from online_dekanat.ocr.paddleocr_client import PaddleOCRClient


@pytest.fixture(autouse=True)
def isolate_compat_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ONLINE_DEKANAT_STORAGE_ROOT", str(tmp_path / "secure"))
    monkeypatch.setenv("ONLINE_DEKANAT_DB_PATH", str(tmp_path / "students.db"))
    monkeypatch.setenv("ONLINE_DEKANAT_RESULTS_PATH", str(tmp_path / "results.json"))


def test_supported_python_version() -> None:
    assert sys.version_info >= (3, 11)


def test_paddle_extras_are_limited_to_python_versions_with_paddlepaddle() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    for extra in ("paddleocr", "ocr"):
        for package in ("paddleocr", "paddlepaddle"):
            requirement = next(item for item in extras[extra] if item.startswith(package))
            assert "; python_version < '3.14'" in requirement


def test_paddleocr_import_error_is_actionable_on_python_314(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def import_without_paddleocr(name: str, *args: object, **kwargs: object) -> object:
        if name == "paddleocr":
            raise ImportError("PaddleOCR is unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_paddleocr)
    monkeypatch.setattr(sys, "version_info", (3, 14))

    with pytest.raises(RuntimeError, match="not included in the project extras on Python 3.14"):
        PaddleOCRClient().recognize("unused")


def test_database_roundtrip_releases_file(tmp_path: Path) -> None:
    db_path = tmp_path / "students.db"
    db = StudentDatabase(path=db_path, key=b"A" * 43 + b"=")
    card = extract_card(Path("samples/study_certificate.txt").read_text(encoding="utf-8"), load_document_types(), "study_certificate")
    result = PipelineResult(card=card, answer=Answer(text="ok"))
    db.save_result(result)
    assert len(db.list_students()) == 1
    assert len(db.list_documents()) == 1
    # The connection must be closed, otherwise Windows cannot delete the file.
    db_path.unlink()
    assert not db_path.exists()
