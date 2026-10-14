from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet

from online_dekanat.models import PipelineResult


class StudentDatabase:
    """SQLite index with Fernet encrypted student and document payloads."""

    def __init__(self, path: str | Path | None = None, key: bytes | None = None) -> None:
        default = Path(os.getenv("ONLINE_DEKANAT_DB_PATH", "data/students.db"))
        self.path = Path(path or default)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(key or _storage_key())
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lookup_hash TEXT NOT NULL UNIQUE,
                    encrypted_payload BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER,
                    result_id TEXT NOT NULL UNIQUE,
                    encrypted_payload BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(student_id) REFERENCES students(id)
                );
                CREATE INDEX IF NOT EXISTS idx_documents_student ON documents(student_id);
            """)

    def save_result(self, result: PipelineResult) -> str:
        now = datetime.now(timezone.utc).isoformat()
        result_id = result.result_id or hashlib.sha256(f"{now}:{result.card.doc_type}:{result.card.raw_text}".encode()).hexdigest()[:20]
        student_name = result.card.fields.get("student_name")
        student_id: int | None = None
        payload = result.to_dict()
        payload["result_id"] = result_id
        payload["processed_at"] = now
        with self._connect() as db:
            if student_name and student_name.is_found:
                normalized = " ".join(student_name.value.lower().split())
                lookup_hash = hashlib.sha256((_storage_key().decode() + normalized).encode()).hexdigest()
                encrypted_student = self._encrypt({"name": student_name.value, "fields": {k: v for k, v in payload["card"]["fields"].items()}})
                db.execute("""
                    INSERT INTO students(lookup_hash, encrypted_payload, created_at, updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(lookup_hash) DO UPDATE SET encrypted_payload=excluded.encrypted_payload, updated_at=excluded.updated_at
                """, (lookup_hash, encrypted_student, now, now))
                student_id = db.execute("SELECT id FROM students WHERE lookup_hash=?", (lookup_hash,)).fetchone()["id"]
            db.execute("INSERT OR REPLACE INTO documents(student_id,result_id,encrypted_payload,created_at) VALUES(?,?,?,?)", (student_id, result_id, self._encrypt(payload), now))
        return result_id

    def list_students(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, encrypted_payload, created_at, updated_at FROM students ORDER BY updated_at DESC").fetchall()
        return [{"id": row["id"], **self._decrypt(row["encrypted_payload"]), "created_at": row["created_at"], "updated_at": row["updated_at"]} for row in rows]

    def list_documents(self, student_id: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if student_id is None:
                rows = db.execute("SELECT encrypted_payload FROM documents ORDER BY created_at DESC").fetchall()
            else:
                rows = db.execute("SELECT encrypted_payload FROM documents WHERE student_id=? ORDER BY created_at DESC", (student_id,)).fetchall()
        return [self._decrypt(row["encrypted_payload"]) for row in rows]

    def _encrypt(self, value: dict[str, Any]) -> bytes:
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def _decrypt(self, value: bytes) -> dict[str, Any]:
        return json.loads(self.cipher.decrypt(value).decode())


def _storage_key() -> bytes:
    configured = os.getenv("ONLINE_DEKANAT_STORAGE_KEY", "").strip()
    if configured:
        return configured.encode("ascii")
    root = Path(os.getenv("ONLINE_DEKANAT_STORAGE_ROOT", "data/secure"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".storage.key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    return key
