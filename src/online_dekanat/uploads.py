from pathlib import Path

from online_dekanat.io import IMAGE_EXTENSIONS

MAX_UPLOAD_SIZE = 16 * 1024 * 1024
SUPPORTED_UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf", ".txt"}


def validate_upload(filename: str, size: int | None = None) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise ValueError(
            "Формат файла не поддерживается. Загрузите JPG, PNG, WEBP, BMP, TIFF, PDF или TXT."
        )
    if size is not None and size > MAX_UPLOAD_SIZE:
        raise ValueError("Размер файла не должен превышать 16 МБ.")
