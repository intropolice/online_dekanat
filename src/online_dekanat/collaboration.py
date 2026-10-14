from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator
from uuid import uuid4

from cryptography.fernet import Fernet

from online_dekanat.database import _storage_key

EMAIL_PATTERN = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
PASSWORD_MIN_LENGTH = 12


class CollaborationStore:
    """Persistent employee accounts and visitor/staff conversations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("ONLINE_DEKANAT_DB_PATH", "data/students.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_key = _storage_key()
        self._cipher = Fernet(self._storage_key)
        self._initialize()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
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
                CREATE TABLE IF NOT EXISTS staff_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'staff')),
                    email_verified INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    visitor_token_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_files (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    storage_name TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    sender_kind TEXT NOT NULL CHECK(sender_kind IN ('visitor', 'staff')),
                    sender_id INTEGER,
                    body BLOB NOT NULL,
                    file_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
                    FOREIGN KEY(sender_id) REFERENCES staff_users(id),
                    FOREIGN KEY(file_id) REFERENCES chat_files(id)
                );
                CREATE INDEX IF NOT EXISTS idx_chat_conversations_updated
                    ON conversations(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
                    ON chat_messages(conversation_id, id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(staff_users)")}
            if "email_verified" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
            if "must_change_password" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")

    def create_admin(
        self,
        email: str,
        password: str,
        *,
        must_change_password: bool = False,
        deliver_temporary_password: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        email = self.validate_email(email)
        self._validate_password(password)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM staff_users WHERE role='admin'").fetchone():
                raise ValueError("Администратор уже создан")
            user = self._create_user(db, email, password, "admin", must_change_password)
            if deliver_temporary_password is not None:
                deliver_temporary_password(password)
            return user

    def create_staff(
        self,
        email: str,
        password: str,
        *,
        must_change_password: bool = False,
        deliver_temporary_password: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        email = self.validate_email(email)
        self._validate_password(password)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = self._create_user(db, email, password, "staff", must_change_password)
            if deliver_temporary_password is not None:
                deliver_temporary_password(password)
            return user

    def create_staff_with_temporary_password(
        self, email: str, deliver_temporary_password: Callable[[str], None]
    ) -> dict[str, Any]:
        email = self.validate_email(email)
        temporary_password = generate_temporary_password()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = self._create_user(db, email, temporary_password, "staff", False)
            deliver_temporary_password(temporary_password)
            return user

    @staticmethod
    def validate_email(email: str) -> str:
        normalized = email.strip().casefold()
        if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("Введите корректный адрес электронной почты")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"Пароль должен содержать не менее {PASSWORD_MIN_LENGTH} символов")
        if len(password) > 1024:
            raise ValueError("Пароль слишком длинный")

    def _create_user(
        self,
        db: sqlite3.Connection,
        email: str,
        password: str,
        role: str,
        must_change_password: bool,
    ) -> dict[str, Any]:
        salt = secrets.token_bytes(16)
        password_hash = _password_hash(password, salt)
        created_at = _now()
        try:
            cursor = db.execute(
                """INSERT INTO staff_users(username,password_hash,password_salt,role,
                   email_verified,must_change_password,created_at) VALUES(?,?,?,?,1,?,?)""",
                (email, password_hash, salt, role, int(must_change_password), created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Учетная запись с таким адресом уже существует") from exc
        return {
            "id": cursor.lastrowid,
            "username": email,
            "email": email,
            "role": role,
            "must_change_password": must_change_password,
            "created_at": created_at,
        }

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        if len(password) > 1024:
            return None
        with self._connect() as db:
            row = db.execute(
                """SELECT id,username,password_hash,password_salt,role,created_at,
                          email_verified,must_change_password,disabled
                   FROM staff_users WHERE username=? COLLATE NOCASE""",
                (username,),
            ).fetchone()
        if row is None:
            _password_hash(password, b"\0" * 16)
            return None
        candidate = _password_hash(password, row["password_salt"])
        if row["disabled"] or not row["email_verified"] or not hmac.compare_digest(candidate, row["password_hash"]):
            return None
        return self._public_user(row)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id,username,role,created_at,email_verified,must_change_password,disabled
                   FROM staff_users WHERE id=?""",
                (user_id,),
            ).fetchone()
        return self._public_user(row) if row and not row["disabled"] else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT id,username,role,created_at,email_verified,must_change_password,disabled
                   FROM staff_users ORDER BY created_at"""
            ).fetchall()
        return [{**self._public_user(row), "disabled": bool(row["disabled"])} for row in rows]

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["username"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
            "created_at": row["created_at"],
        }

    def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        self._validate_password(new_password)
        if current_password == new_password:
            raise ValueError("Новый пароль должен отличаться от текущего")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT password_hash,password_salt FROM staff_users WHERE id=? AND disabled=0",
                (user_id,),
            ).fetchone()
            if row is None or not hmac.compare_digest(
                _password_hash(current_password, row["password_salt"]), row["password_hash"]
            ):
                return False
            salt = secrets.token_bytes(16)
            db.execute(
                """UPDATE staff_users SET password_hash=?,password_salt=?,must_change_password=0
                   WHERE id=?""",
                (_password_hash(new_password, salt), salt, user_id),
            )
            return True

    def create_conversation(self, visitor_token: str) -> dict[str, Any]:
        conversation_id = uuid4().hex
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO conversations(id,visitor_token_hash,created_at,updated_at)
                   VALUES(?,?,?,?)""",
                (conversation_id, _token_hash(visitor_token), now, now),
            )
        return {"id": conversation_id, "status": "open", "created_at": now, "updated_at": now}

    def visitor_owns(self, conversation_id: str, visitor_token: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT visitor_token_hash FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return bool(row and hmac.compare_digest(row["visitor_token_hash"], _token_hash(visitor_token)))

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone() is not None

    def add_message(
        self,
        conversation_id: str,
        sender_kind: str,
        body: str,
        *,
        sender_id: int | None = None,
        file: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not body.strip() and file is None:
            raise ValueError("Введите сообщение или прикрепите файл")
        if len(body) > 20_000:
            raise ValueError("Сообщение не должно превышать 20 000 символов")
        now = _now()
        with self._connect() as db:
            if file:
                db.execute(
                    """INSERT INTO chat_files(id,conversation_id,storage_name,original_name,
                       media_type,size,created_at) VALUES(?,?,?,?,?,?,?)""",
                    (
                        file["id"],
                        conversation_id,
                        file["storage_name"],
                        file["original_name"],
                        file["media_type"],
                        file["size"],
                        now,
                    ),
                )
            cursor = db.execute(
                """INSERT INTO chat_messages(conversation_id,sender_kind,sender_id,body,file_id,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    conversation_id,
                    sender_kind,
                    sender_id,
                    self._cipher.encrypt(body.strip().encode("utf-8")),
                    file["id"] if file else None,
                    now,
                ),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (now, conversation_id),
            )
            message_id = cursor.lastrowid
        return self.get_message(message_id)

    def get_message(self, message_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT m.id,m.conversation_id,m.sender_kind,m.body,m.created_at,
                          u.username,f.id AS file_id,f.original_name,f.media_type,f.size
                   FROM chat_messages m
                   LEFT JOIN staff_users u ON u.id=m.sender_id
                   LEFT JOIN chat_files f ON f.id=m.file_id
                   WHERE m.id=?""",
                (message_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Сообщение чата не найдено")
        message = {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "sender_kind": row["sender_kind"],
            "sender_name": row["username"] or "Посетитель",
            "body": self._cipher.decrypt(row["body"]).decode("utf-8"),
            "created_at": row["created_at"],
            "file": None,
        }
        if row["file_id"]:
            message["file"] = {
                "id": row["file_id"],
                "name": row["original_name"],
                "media_type": row["media_type"],
                "size": row["size"],
                "url": f"/chat/files/{row['file_id']}",
            }
        return message

    def list_messages(self, conversation_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            ids = db.execute(
                """SELECT id FROM chat_messages
                   WHERE conversation_id=? AND id>? ORDER BY id LIMIT 500""",
                (conversation_id, after),
            ).fetchall()
        return [self.get_message(row["id"]) for row in ids]

    def list_conversations(self, *, include_closed: bool = False) -> list[dict[str, Any]]:
        where = "" if include_closed else "WHERE c.status='open'"
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT c.id,c.status,c.created_at,c.updated_at,
                           (SELECT id FROM chat_messages m WHERE m.conversation_id=c.id
                            ORDER BY m.id DESC LIMIT 1) AS last_message_id,
                           (SELECT sender_kind FROM chat_messages m WHERE m.conversation_id=c.id
                            ORDER BY m.id DESC LIMIT 1) AS last_sender
                    FROM conversations c {where}
                    ORDER BY c.updated_at DESC LIMIT 200"""
            ).fetchall()
        conversations = []
        for row in rows:
            item = dict(row)
            message_id = item.pop("last_message_id")
            if message_id is None:
                item["last_message"] = ""
            else:
                latest = self.get_message(message_id)
                item["last_message"] = latest["body"] or (
                    f"Файл: {latest['file']['name']}" if latest["file"] else ""
                )
            conversations.append(item)
        return conversations

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id,conversation_id,storage_name,original_name,media_type,size
                   FROM chat_files WHERE id=?""",
                (file_id,),
            ).fetchone()
        return dict(row) if row else None


def session_secret() -> str:
    configured = os.getenv("ONLINE_DEKANAT_SESSION_SECRET", "").strip()
    if configured:
        if len(configured) < 32:
            raise ValueError("ONLINE_DEKANAT_SESSION_SECRET должен содержать не менее 32 символов")
        return configured

    root = Path(os.getenv("ONLINE_DEKANAT_STORAGE_ROOT", "data/secure"))
    root.mkdir(parents=True, exist_ok=True)
    key_path = root / ".session.key"
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return key_path.read_text(encoding="ascii").strip()
    secret = secrets.token_urlsafe(48)
    with os.fdopen(descriptor, "w", encoding="ascii") as key_file:
        key_file.write(secret)
    return secret


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)


def generate_temporary_password() -> str:
    alphabet_groups = (
        "abcdefghijklmnopqrstuvwxyz",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "0123456789",
        "!@#$%^&*()-_=+",
    )
    password = [secrets.choice(group) for group in alphabet_groups]
    alphabet = "".join(alphabet_groups)
    password.extend(secrets.choice(alphabet) for _ in range(20))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
