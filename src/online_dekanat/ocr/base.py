from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class OCRResult:
    text: str
    engine: str
    pages: int = 1
    metadata: dict[str, str] = field(default_factory=dict)


class OCRClient(Protocol):
    def recognize(self, file_path: str | Path) -> OCRResult:
        """Recognize text from an image-like file."""
