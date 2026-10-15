import json
import logging
from pathlib import Path

from online_dekanat.results import append_result


def test_append_result_writes_atomically_in_target_directory(tmp_path: Path) -> None:
    target = tmp_path / "results.json"

    assert append_result({"result_id": "first"}, target)
    assert append_result({"result_id": "second"}, target)

    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"result_id": "first"},
        {"result_id": "second"},
    ]
    assert list(tmp_path.iterdir()) == [target]


def test_append_result_permission_error_does_not_raise_or_hide_warning(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    target = tmp_path / "results.json"
    target.write_text("[]", encoding="utf-8")
    original_write_text = Path.write_text

    def deny_target_write(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError("permission denied")
        return original_write_text(path, *args, **kwargs)

    def deny_replace(source: Path, destination: Path) -> None:
        assert source != destination
        raise PermissionError("replace denied")

    monkeypatch.setattr(Path, "write_text", deny_target_write)
    monkeypatch.setattr("online_dekanat.results.os.replace", deny_replace)

    with caplog.at_level(logging.WARNING, logger="online_dekanat.results"):
        assert append_result({"result_id": "first"}, target) is False

    assert "Не удалось записать журнал результатов" in caplog.text
    assert json.loads(target.read_text(encoding="utf-8")) == []
    assert len(list(tmp_path.iterdir())) == 1
