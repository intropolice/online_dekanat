from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_document_types(config_dir: Path | None = None) -> dict[str, Any]:
    return load_json((config_dir or DEFAULT_CONFIG_DIR) / "document_types.json")


def load_regulations(config_dir: Path | None = None) -> list[dict[str, Any]]:
    return load_json((config_dir or DEFAULT_CONFIG_DIR) / "regulations.json")
