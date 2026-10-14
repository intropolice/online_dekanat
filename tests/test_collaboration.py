from pathlib import Path
import sqlite3

import pytest

from online_dekanat.collaboration import CollaborationStore


def test_employee_email_validation_and_password_policy(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    admin = store.create_admin("ADMIN@ADM.RKSI.RU", "a sufficiently long password")

    assert admin["role"] == "admin"
    assert store.authenticate("admin@adm.rksi.ru", "a sufficiently long password")["id"] == admin["id"]
    assert store.authenticate("admin@adm.rksi.ru", "wrong password") is None
    assert store.validate_email("employee@gmail.com") == "employee@gmail.com"
    assert store.validate_email("employee@mail.ru") == "employee@mail.ru"
    with pytest.raises(ValueError, match="корректный адрес"):
        store.validate_email("not-an-email")
    with pytest.raises(ValueError, match="Администратор уже создан"):
        store.create_admin("another@adm.rksi.ru", "another sufficiently long password")
    with pytest.raises(ValueError, match="не менее 12"):
        store.create_staff("employee@adm.rksi.ru", "short")


def test_registration_creates_account_and_delivers_one_time_password(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    email = "employee@mail.ru"
    delivered: list[str] = []
    user = store.create_staff_with_temporary_password(email, delivered.append)

    assert user is not None
    assert len(delivered) == 1
    assert user["must_change_password"] is False
    temporary_password = delivered[0]
    assert len(temporary_password) >= 24
    assert any(char.islower() for char in temporary_password)
    assert any(char.isupper() for char in temporary_password)
    assert any(char.isdigit() for char in temporary_password)
    assert any(not char.isalnum() for char in temporary_password)
    assert store.authenticate(email, temporary_password)["must_change_password"] is False

    with sqlite3.connect(store.path) as db:
        stored_password = db.execute(
            "SELECT password_hash FROM staff_users WHERE username=?", (email,)
        ).fetchone()[0]
        assert temporary_password.encode() not in stored_password


def test_registration_rolls_back_account_when_temporary_password_email_fails(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    email = "employee@mail.ru"

    def reject_email(_password: str) -> None:
        raise RuntimeError("smtp unavailable")

    with pytest.raises(RuntimeError, match="smtp unavailable"):
        store.create_staff_with_temporary_password(email, reject_email)

    assert store.authenticate(email, "anything") is None
    assert store.create_staff_with_temporary_password(email, lambda _password: None) is not None


def test_visitor_token_scopes_conversation_access_and_messages(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    conversation = store.create_conversation("visitor-secret")

    assert store.visitor_owns(conversation["id"], "visitor-secret")
    assert not store.visitor_owns(conversation["id"], "another-visitor")

    message = store.add_message(conversation["id"], "visitor", "Здравствуйте")
    reply = store.add_message(conversation["id"], "staff", "Добрый день", sender_id=None)

    assert message["body"] == "Здравствуйте"
    assert reply["body"] == "Добрый день"
    assert [item["id"] for item in store.list_messages(conversation["id"], after=message["id"])] == [reply["id"]]
    with sqlite3.connect(store.path) as db:
        stored_body = db.execute(
            "SELECT body FROM chat_messages WHERE id=?", (message["id"],)
        ).fetchone()[0]
    assert "Здравствуйте".encode("utf-8") not in stored_body


def test_chat_api_authenticates_staff_registers_emails_and_protects_attachments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ONLINE_DEKANAT_DB_PATH", str(tmp_path / "database.sqlite"))
    monkeypatch.setenv("ONLINE_DEKANAT_STORAGE_ROOT", str(tmp_path / "secure"))
    monkeypatch.setenv("ONLINE_DEKANAT_SESSION_SECRET", "test-session-secret-that-is-long-enough")

    from online_dekanat import api

    store = CollaborationStore(tmp_path / "database.sqlite")
    admin = store.create_admin("admin@adm.rksi.ru", "a sufficiently long password")
    monkeypatch.setattr(api, "_collaboration", store)
    monkeypatch.setattr(api, "_database", api.StudentDatabase(tmp_path / "database.sqlite"))
    sent_passwords: dict[str, str] = {}
    monkeypatch.setattr(
        api,
        "send_temporary_password",
        lambda email, password: sent_passwords.__setitem__(email, password),
    )

    with (
        TestClient(api.app) as visitor,
        TestClient(api.app) as staff,
        TestClient(api.app) as admin_client,
        TestClient(api.app) as other,
    ):
        assert visitor.get("/students").status_code == 401
        assert visitor.post(
            "/auth/register/request", json={"email": "employee@mail.ru"}
        ).status_code == 201
        registration_email = "employee@mail.ru"
        assert registration_email in sent_passwords
        temporary_password = sent_passwords[registration_email]
        with sqlite3.connect(store.path) as db:
            db.execute(
                "UPDATE staff_users SET must_change_password=1 WHERE username=?",
                (registration_email,),
            )
        duplicate = visitor.post(
            "/auth/register/request", json={"email": registration_email}
        )
        assert duplicate.status_code == 409

        assert staff.post(
            "/auth/login",
            json={"email": registration_email, "password": temporary_password},
        ).status_code == 200
        assert staff.get("/chat/conversations").status_code == 200
        assert staff.post(
            "/auth/change-password",
            json={
                "current_password": temporary_password,
                "new_password": "a permanent and very secure password",
            },
        ).status_code == 200
        assert staff.get("/chat/conversations").status_code == 200
        assert staff.get("/auth/users").status_code == 403

        conversation = visitor.post("/chat/conversations").json()["conversation"]["id"]
        uploaded = visitor.post(
            f"/chat/conversations/{conversation}/messages",
            data={"body": "Проверьте файл"},
            files={"file": ("document.txt", b"private attachment", "text/plain")},
        )
        assert uploaded.status_code == 200
        file_info = uploaded.json()["file"]
        assert visitor.get(file_info["url"]).content == b"private attachment"
        assert other.get(file_info["url"]).status_code == 404
        assert other.get(f"/chat/conversations/{conversation}/messages").status_code == 404

        assert staff.get("/chat/conversations").json()[0]["id"] == conversation
        assert staff.get(f"/chat/conversations/{conversation}/messages").json()[0]["body"] == "Проверьте файл"
        with visitor.websocket_connect(f"/chat/ws/{conversation}") as visitor_socket:
            with staff.websocket_connect(f"/chat/ws/{conversation}") as staff_socket:
                visitor_reply = visitor.post(
                    f"/chat/conversations/{conversation}/messages",
                    data={"body": "Ответ посетителя"},
                )
                assert visitor_reply.status_code == 200
                assert staff_socket.receive_json()["message"]["body"] == "Ответ посетителя"
                assert visitor_socket.receive_json()["message"]["body"] == "Ответ посетителя"
                staff_reply = staff.post(
                    f"/chat/conversations/{conversation}/messages",
                    data={"body": "Ответ сотрудника"},
                )
                assert staff_reply.status_code == 200
                assert visitor_socket.receive_json()["message"]["body"] == "Ответ сотрудника"

        assert visitor.get(f"/chat/conversations/{conversation}/messages").json()[-1]["body"] == "Ответ сотрудника"
        assert admin_client.post(
            "/auth/login",
            json={"email": "admin@adm.rksi.ru", "password": "a sufficiently long password"},
        ).status_code == 200
        assert admin_client.post(
            "/auth/users", json={"email": "invited@adm.rksi.ru"}
        ).status_code == 200
        assert "invited@adm.rksi.ru" in sent_passwords
        assert admin_client.get("/auth/users").status_code == 200

        assert other.post(
            "/auth/login",
            json={
                "email": "invited@adm.rksi.ru",
                "password": sent_passwords["invited@adm.rksi.ru"],
            },
        ).status_code == 200
        assert other.get("/auth/users").status_code == 403
        assert other.get("/students").status_code == 200
        assert other.post(
            "/auth/change-password",
            json={
                "current_password": sent_passwords["invited@adm.rksi.ru"],
                "new_password": "another permanent secure password",
            },
        ).status_code == 200
        assert other.get("/students").status_code == 200

    encrypted_files = list((tmp_path / "secure").glob("*.enc"))
    assert len(encrypted_files) == 1
    assert b"private attachment" not in encrypted_files[0].read_bytes()
    assert admin["role"] == "admin"
