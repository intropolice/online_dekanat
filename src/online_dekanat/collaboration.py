from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
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
        self._log_root = Path(
            os.getenv("ONLINE_DEKANAT_CHAT_LOG_DIR", self.path.parent / "conversations")
        )
        self._log_lock = threading.RLock()
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
                    profile_name BLOB,
                    avatar BLOB,
                    avatar_media_type TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    code TEXT UNIQUE,
                    visitor_token_hash TEXT NOT NULL,
                    assigned_staff_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'closed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(assigned_staff_id) REFERENCES staff_users(id)
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
            conversation_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(conversations)")
            }
            if "code" not in conversation_columns:
                db.execute("ALTER TABLE conversations ADD COLUMN code TEXT")
            if "assigned_staff_id" not in conversation_columns:
                db.execute(
                    "ALTER TABLE conversations ADD COLUMN assigned_staff_id INTEGER"
                )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_conversation_code "
                "ON conversations(code)"
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(staff_users)")}
            if "profile_name" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN profile_name BLOB")
            if "avatar" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN avatar BLOB")
            if "avatar_media_type" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN avatar_media_type TEXT")
            if "email_verified" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
            if "must_change_password" not in columns:
                db.execute("ALTER TABLE staff_users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
            for row in db.execute(
                "SELECT id FROM conversations WHERE code IS NULL OR code=''"
            ).fetchall():
                code = row["id"][:8]
                if db.execute(
                    "SELECT 1 FROM conversations WHERE code=?", (code,)
                ).fetchone():
                    code = secrets.token_hex(4)
                    while db.execute(
                        "SELECT 1 FROM conversations WHERE code=?", (code,)
                    ).fetchone():
                        code = secrets.token_hex(4)
                db.execute(
                    "UPDATE conversations SET code=? WHERE id=?", (code, row["id"])
                )
            conversation_ids = [
                row["id"] for row in db.execute("SELECT id FROM conversations")
            ]
        for conversation_id in conversation_ids:
            self._write_conversation_log(conversation_id)

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
            "full_name": "",
            "avatar_url": None,
            "must_change_password": must_change_password,
            "created_at": created_at,
        }

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        if len(password) > 1024:
            return None
        with self._connect() as db:
            row = db.execute(
                """SELECT id,username,password_hash,password_salt,role,created_at,
                          profile_name,avatar,email_verified,must_change_password,disabled
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
                          ,profile_name,avatar
                   FROM staff_users WHERE id=?""",
                (user_id,),
            ).fetchone()
        return self._public_user(row) if row and not row["disabled"] else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT id,username,role,created_at,email_verified,must_change_password,disabled
                          ,profile_name,avatar
                   FROM staff_users ORDER BY created_at"""
            ).fetchall()
        return [{**self._public_user(row), "disabled": bool(row["disabled"])} for row in rows]

    def _public_user(self, row: sqlite3.Row) -> dict[str, Any]:
        encrypted_name = row["profile_name"] if "profile_name" in row.keys() else None
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["username"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
            "full_name": (
                self._cipher.decrypt(encrypted_name).decode("utf-8")
                if encrypted_name
                else ""
            ),
            "avatar_url": "/auth/profile/avatar" if "avatar" in row.keys() and row["avatar"] else None,
            "created_at": row["created_at"],
        }

    def update_profile(
        self,
        user_id: int,
        full_name: str,
        *,
        avatar: bytes | None = None,
        avatar_media_type: str | None = None,
        remove_avatar: bool = False,
        current_password: str = "",
        new_password: str = "",
    ) -> dict[str, Any]:
        full_name = full_name.strip()
        if len(full_name) < 2 or len(full_name) > 150:
            raise ValueError("ФИО должно содержать от 2 до 150 символов")
        if any(ord(character) < 32 for character in full_name):
            raise ValueError("ФИО содержит недопустимые символы")
        if (avatar is None) != (avatar_media_type is None):
            raise ValueError("Не удалось проверить аватар")
        if avatar_media_type not in (None, "image/webp"):
            raise ValueError("Аватар должен быть изображением WebP")
        if new_password:
            self._validate_password(new_password)
            if not current_password:
                raise ValueError("Введите текущий пароль")
            if current_password == new_password:
                raise ValueError("Новый пароль должен отличаться от текущего")
        elif current_password:
            raise ValueError("Введите новый пароль")

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT password_hash,password_salt FROM staff_users
                   WHERE id=? AND disabled=0""",
                (user_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Учетная запись не найдена")
            if new_password and not hmac.compare_digest(
                _password_hash(current_password, row["password_salt"]),
                row["password_hash"],
            ):
                raise PermissionError("Текущий пароль указан неверно")

            fields = ["profile_name=?"]
            values: list[Any] = [
                self._cipher.encrypt(full_name.encode("utf-8"))
            ]
            if avatar is not None:
                fields.extend(["avatar=?", "avatar_media_type=?"])
                values.extend([self._cipher.encrypt(avatar), avatar_media_type])
            elif remove_avatar:
                fields.extend(["avatar=NULL", "avatar_media_type=NULL"])
            if new_password:
                salt = secrets.token_bytes(16)
                fields.extend(
                    ["password_hash=?", "password_salt=?", "must_change_password=0"]
                )
                values.extend([_password_hash(new_password, salt), salt])
            values.append(user_id)
            db.execute(
                f"UPDATE staff_users SET {', '.join(fields)} WHERE id=?",
                values,
            )
        user = self.get_user(user_id)
        if user is None:
            raise LookupError("Учетная запись не найдена")
        return user

    def get_profile_avatar(self, user_id: int) -> tuple[bytes, str] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT avatar,avatar_media_type FROM staff_users
                   WHERE id=? AND disabled=0""",
                (user_id,),
            ).fetchone()
        if row is None or row["avatar"] is None:
            return None
        return self._cipher.decrypt(row["avatar"]), row["avatar_media_type"]

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
            db.execute("BEGIN IMMEDIATE")
            code = secrets.token_hex(4)
            while db.execute(
                "SELECT 1 FROM conversations WHERE code=?", (code,)
            ).fetchone():
                code = secrets.token_hex(4)
            db.execute(
                """INSERT INTO conversations(id,code,visitor_token_hash,created_at,updated_at)
                   VALUES(?,?,?,?,?)""",
                (conversation_id, code, _token_hash(visitor_token), now, now),
            )
        self._write_conversation_log(conversation_id)
        return {
            "id": conversation_id,
            "code": code,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }

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

    def staff_can_access(self, conversation_id: str, staff_id: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT assigned_staff_id FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return bool(
            row is not None
            and row["assigned_staff_id"] in (None, staff_id)
        )

    def claim_conversation(
        self, conversation_id: str, staff_id: int
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT assigned_staff_id FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Обращение не найдено")
            if row["assigned_staff_id"] == staff_id:
                return self._conversation_details(db, conversation_id)
            if row["assigned_staff_id"] is not None:
                raise ValueError("Обращение уже закреплено за другим сотрудником")
            if db.execute(
                """SELECT 1 FROM chat_messages
                   WHERE conversation_id=? AND sender_kind='staff' LIMIT 1""",
                (conversation_id,),
            ).fetchone():
                raise ValueError("На это обращение уже ответили")
            db.execute(
                """UPDATE conversations SET assigned_staff_id=?,updated_at=?
                   WHERE id=? AND assigned_staff_id IS NULL""",
                (staff_id, now, conversation_id),
            )
            conversation = self._conversation_details(db, conversation_id)
        self._write_conversation_log(conversation_id)
        return conversation

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
            db.execute("BEGIN IMMEDIATE")
            conversation = db.execute(
                "SELECT assigned_staff_id FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise LookupError("Обращение не найдено")
            if sender_kind == "staff" and sender_id is not None:
                assigned_staff_id = conversation["assigned_staff_id"]
                if assigned_staff_id not in (None, sender_id):
                    raise PermissionError(
                        "Обращение закреплено за другим сотрудником"
                    )
                if assigned_staff_id is None:
                    db.execute(
                        """UPDATE conversations SET assigned_staff_id=?
                           WHERE id=? AND assigned_staff_id IS NULL""",
                        (sender_id, conversation_id),
                    )
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
        message = self.get_message(message_id)
        self._write_conversation_log(conversation_id)
        return message

    def get_message(self, message_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT m.id,m.conversation_id,m.sender_kind,m.body,m.created_at,
                          u.username,u.profile_name,f.id AS file_id,f.original_name,f.media_type,f.size
                   FROM chat_messages m
                   LEFT JOIN staff_users u ON u.id=m.sender_id
                   LEFT JOIN chat_files f ON f.id=m.file_id
                   WHERE m.id=?""",
                (message_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Сообщение чата не найдено")
        sender_name = row["username"] or "Посетитель"
        if row["profile_name"]:
            sender_name = self._cipher.decrypt(row["profile_name"]).decode("utf-8")
        message = {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "sender_kind": row["sender_kind"],
            "sender_name": sender_name,
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

    def list_conversations(
        self, *, staff_id: int, include_closed: bool = False
    ) -> list[dict[str, Any]]:
        filters = ["(c.assigned_staff_id IS NULL OR c.assigned_staff_id=?)"]
        parameters: list[Any] = [staff_id]
        if not include_closed:
            filters.append("c.status='open'")
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT c.id,c.code,c.status,c.assigned_staff_id,
                           assigned.username AS assigned_staff_email,
                           c.created_at,c.updated_at,
                           (SELECT id FROM chat_messages m WHERE m.conversation_id=c.id
                            ORDER BY m.id DESC LIMIT 1) AS last_message_id,
                           (SELECT sender_kind FROM chat_messages m WHERE m.conversation_id=c.id
                            ORDER BY m.id DESC LIMIT 1) AS last_sender,
                           EXISTS(SELECT 1 FROM chat_messages m
                                  WHERE m.conversation_id=c.id AND m.sender_kind='staff')
                               AS has_staff_reply
                    FROM conversations c
                    LEFT JOIN staff_users assigned ON assigned.id=c.assigned_staff_id
                    WHERE {' AND '.join(filters)}
                    ORDER BY c.updated_at DESC LIMIT 200"""
                , parameters
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
            item["can_claim"] = (
                item["assigned_staff_id"] is None and not item["has_staff_reply"]
            )
            conversations.append(item)
        return conversations

    def _conversation_details(
        self, db: sqlite3.Connection, conversation_id: str
    ) -> dict[str, Any]:
        row = db.execute(
            """SELECT c.id,c.code,c.status,c.assigned_staff_id,
                      u.username AS assigned_staff_email,c.created_at,c.updated_at
               FROM conversations c
               LEFT JOIN staff_users u ON u.id=c.assigned_staff_id
               WHERE c.id=?""",
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Обращение не найдено")
        return dict(row)

    def _write_conversation_log(self, conversation_id: str) -> None:
        with self._log_lock:
            with self._connect() as db:
                conversation = self._conversation_details(db, conversation_id)
            messages = self.list_messages(conversation_id)
            self._log_root.mkdir(parents=True, exist_ok=True)
            path = self._log_root / f"{conversation['code']}.json"
            payload = json.dumps(
                {"conversation": conversation, "messages": messages},
                ensure_ascii=False,
                indent=2,
            )
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{conversation['code']}-", suffix=".tmp", dir=self._log_root
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, path)
            except Exception:
                Path(temporary_name).unlink(missing_ok=True)
                raise

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
