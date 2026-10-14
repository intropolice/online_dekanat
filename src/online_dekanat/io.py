from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def load_pages(file_path: str | Path, dpi: int = 180) -> list[Image.Image]:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        with Image.open(path) as image:
            return [
                ImageOps.exif_transpose(page).convert("RGB").copy()
                for page in ImageSequence.Iterator(image)
            ]
    if suffix == ".pdf":
        return _load_pdf_pages(path, dpi=dpi)
    raise ValueError(f"Unsupported file format: {path.suffix}")


def _load_pdf_pages(path: Path, dpi: int) -> list[Image.Image]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF input requires pymupdf. Install the base project dependencies.") from exc

    pages: list[Image.Image] = []
    document = fitz.open(path)
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            pages.append(image.copy())
    finally:
        document.close()
    return pages
