from __future__ import annotations

import base64
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


MAGIC = b"ONLINE-DEKANAT-ENCRYPTED\n"


class EncryptedStorage:
    """Encrypt uploaded documents at rest and expose them only briefly to OCR."""

    def __init__(self, root: str | Path = "data/secure") -> None:
        self.root = Path(root)
        self.key: bytes | None = None

    def save(self, payload: bytes, suffix: str = ".bin") -> Path:
        from cryptography.fernet import Fernet

        self.root.mkdir(parents=True, exist_ok=True)
        key = self._get_key()
        safe_suffix = suffix if suffix and suffix.startswith(".") else ".bin"
        path = self.root / f"{uuid4().hex}{safe_suffix}.enc"
        token = Fernet(key).encrypt(payload)
        path.write_bytes(MAGIC + safe_suffix.encode("ascii", errors="ignore") + b"\n" + token)
        return path

    @contextmanager
    def decrypted_temporary_file(self, encrypted_path: str | Path) -> Iterator[Path]:
        path = Path(encrypted_path)
        raw = path.read_bytes()
        if not raw.startswith(MAGIC):
            yield path
            return
        from cryptography.fernet import Fernet, InvalidToken
        try:
            header, token = raw[len(MAGIC):].split(b"\n", 1)
            suffix = header.decode("ascii") or ".bin"
            payload = Fernet(self._get_key()).decrypt(token)
        except (ValueError, InvalidToken) as exc:
            raise ValueError("Зашифрованный файл повреждён или создан другим ключом") from exc
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(payload)
            plain_path = Path(tmp.name)
        try:
            yield plain_path
        finally:
            plain_path.unlink(missing_ok=True)

    def _get_key(self) -> bytes:
        if self.key is None:
            self.root.mkdir(parents=True, exist_ok=True)
            self.key = _load_key(self.root)
        return self.key


def _load_key(root: Path) -> bytes:
    configured = os.getenv("ONLINE_DEKANAT_STORAGE_KEY", "").strip()
    if configured:
        try:
            base64.urlsafe_b64decode(configured.encode("ascii"))
        except Exception as exc:
            raise ValueError("ONLINE_DEKANAT_STORAGE_KEY должен быть ключом Fernet") from exc
        return configured.encode("ascii")
    key_path = root / ".storage.key"
    if key_path.exists():
        return key_path.read_bytes().strip()
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    key_path.write_bytes(key)
    return key
