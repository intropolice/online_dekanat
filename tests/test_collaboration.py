from pathlib import Path
import sqlite3
import json
from io import BytesIO

import pytest
from PIL import Image

from online_dekanat.collaboration import CollaborationStore


def test_employee_email_validation_and_password_policy(tmp_path: Path) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    admin = store.create_admin("ADMIN@ADM.RKSI.RU", "a sufficiently long password")
    numeric_password_user = store.create_staff("digits@adm.rksi.ru", "987654321098")

    assert admin["role"] == "admin"
    assert store.authenticate("admin@adm.rksi.ru", "a sufficiently long password")["id"] == admin["id"]
    assert store.authenticate("admin@adm.rksi.ru", "wrong password") is None
    assert store.authenticate(numeric_password_user["email"], "987654321098") is not None
    assert store.validate_email("employee@gmail.com") == "employee@gmail.com"
    assert store.validate_email("employee@mail.ru") == "employee@mail.ru"
    with pytest.raises(ValueError, match="корректный адрес"):
        store.validate_email("not-an-email")
    with pytest.raises(ValueError, match="Администратор уже создан"):
        store.create_admin("another@adm.rksi.ru", "another sufficiently long password")
    with pytest.raises(ValueError, match="не менее 12"):
        store.create_staff("employee@adm.rksi.ru", "short")


def test_staff_profile_stores_encrypted_name_avatar_and_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ONLINE_DEKANAT_DB_PATH", str(tmp_path / "database.sqlite"))
    monkeypatch.setenv("ONLINE_DEKANAT_STORAGE_ROOT", str(tmp_path / "secure"))
    monkeypatch.setenv(
        "ONLINE_DEKANAT_SESSION_SECRET", "test-session-secret-that-is-long-enough"
    )

    from online_dekanat import api

    store = CollaborationStore(tmp_path / "database.sqlite")
    staff = store.create_staff("profile@adm.rksi.ru", "a sufficiently secure password")
    monkeypatch.setattr(api, "_collaboration", store)

    image = BytesIO()
    Image.new("RGB", (32, 32), "navy").save(image, format="PNG")

    with TestClient(api.app) as client:
        assert client.get("/auth/profile").status_code == 401
        profile_page = client.get("/staff/profile")
        assert profile_page.status_code == 200
        assert 'id="profile-theme-toggle"' in profile_page.text
        assert 'id="choose-avatar"' in profile_page.text
        assert 'id="profile-email" readonly' not in profile_page.text
        assert profile_page.text.index("</form>") < profile_page.text.index(
            '<form id="password-form">'
        )
        assert 'id="change-password" type="submit">Изменить пароль' in profile_page.text
        assert client.post(
            "/auth/login",
            json={
                "email": staff["email"],
                "password": "a sufficiently secure password",
            },
        ).status_code == 200

        saved_without_password = client.post(
            "/auth/profile",
            data={"full_name": "Иванов Иван Иванович"},
        )
        assert saved_without_password.status_code == 200
        assert store.authenticate(
            staff["email"], "a sufficiently secure password"
        ) is not None

        updated = client.post(
            "/auth/profile",
            data={
                "full_name": "Иванов Иван Иванович",
                "current_password": "a sufficiently secure password",
                "new_password": "a new and very secure password",
            },
            files={"avatar": ("avatar.png", image.getvalue(), "image/png")},
        )
        assert updated.status_code == 200
        user = updated.json()["user"]
        assert user["full_name"] == "Иванов Иван Иванович"
        assert user["avatar_url"] == "/auth/profile/avatar"
        assert store.authenticate(staff["email"], "a sufficiently secure password") is None
        assert store.authenticate(staff["email"], "a new and very secure password") is not None

        avatar = client.get(user["avatar_url"])
        assert avatar.status_code == 200
        assert avatar.headers["content-type"] == "image/webp"
        assert avatar.content.startswith(b"RIFF")

        with sqlite3.connect(store.path) as db:
            stored_name, stored_avatar = db.execute(
                "SELECT profile_name,avatar FROM staff_users WHERE id=?",
                (staff["id"],),
            ).fetchone()
        assert "Иванов".encode("utf-8") not in stored_name
        assert b"RIFF" not in stored_avatar

        rejected = client.post(
            "/auth/profile",
            data={
                "full_name": "Не должно сохраниться",
                "current_password": "wrong password",
                "new_password": "another secure new password",
            },
        )
        assert rejected.status_code == 400
        assert client.get("/auth/profile").json()["user"]["full_name"] == "Иванов Иван Иванович"

        removed = client.post(
            "/auth/profile",
            data={"full_name": "Иванов Иван Иванович", "remove_avatar": "true"},
        )
        assert removed.status_code == 200
        assert removed.json()["user"]["avatar_url"] is None


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


def test_claiming_conversation_is_exclusive_and_updates_its_json_log(
    tmp_path: Path,
) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    first_staff = store.create_staff("first@adm.rksi.ru", "first secure password")
    second_staff = store.create_staff("second@adm.rksi.ru", "second secure password")
    conversation = store.create_conversation("visitor-secret")

    claimed = store.claim_conversation(conversation["id"], first_staff["id"])

    assert claimed["assigned_staff_id"] == first_staff["id"]
    assert claimed["code"] == conversation["code"]
    assert [
        item["id"] for item in store.list_conversations(staff_id=first_staff["id"])
    ] == [conversation["id"]]
    assert store.list_conversations(staff_id=second_staff["id"]) == []
    assert not store.staff_can_access(conversation["id"], second_staff["id"])
    with pytest.raises(ValueError, match="уже закреплено"):
        store.claim_conversation(conversation["id"], second_staff["id"])
    with pytest.raises(PermissionError, match="закреплено"):
        store.add_message(
            conversation["id"],
            "staff",
            "Чужой ответ",
            sender_id=second_staff["id"],
        )
    store.add_message(
        conversation["id"],
        "staff",
        "Ответ закрепленного сотрудника",
        sender_id=first_staff["id"],
    )

    log_path = tmp_path / "conversations" / f"{conversation['code']}.json"
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["conversation"]["assigned_staff_email"] == first_staff["email"]
    assert [message["body"] for message in log["messages"]] == [
        "Ответ закрепленного сотрудника"
    ]
    assert list((tmp_path / "conversations").glob("*.json")) == [log_path]


def test_conversation_cannot_be_claimed_after_staff_has_replied(
    tmp_path: Path,
) -> None:
    store = CollaborationStore(tmp_path / "database.sqlite")
    staff = store.create_staff("staff@adm.rksi.ru", "a secure password here")
    another_staff = store.create_staff("another@adm.rksi.ru", "another secure password")
    conversation = store.create_conversation("visitor-secret")
    store.add_message(
        conversation["id"],
        "staff",
        "Ответ сотрудника",
        sender_id=staff["id"],
    )

    with pytest.raises(ValueError, match="уже закреплено"):
        store.claim_conversation(conversation["id"], another_staff["id"])


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
        from starlette.websockets import WebSocketDisconnect

        staff_page = staff.get("/staff").text
        assert 'body class="staff-page"' in staff_page
        assert '.staff-toolbar-tools [hidden]{display:none!important}' in staff_page
        for element_id in (
            "staff-notifications",
            "staff-sound-toggle",
            "staff-profile-button",
            "staff-logout",
        ):
            assert f'id="{element_id}"' in staff_page
            assert f'id="{element_id}"' in staff_page and " hidden" in staff_page[
                staff_page.index(f'id="{element_id}"') :
                staff_page.index(f'id="{element_id}"') + 150
            ]
        assert 'href="/staff"' in visitor.get("/ui").text
        with pytest.raises(WebSocketDisconnect):
            with other.websocket_connect("/chat/staff/ws"):
                pass
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
        staff_chat = staff.post("/chat/conversations")
        assert staff_chat.status_code == 200
        staff_conversation = staff_chat.json()["conversation"]["id"]
        staff_visitor_message = staff.post(
            f"/chat/conversations/{staff_conversation}/messages",
            data={"body": "Сообщение от пользователя с аккаунтом сотрудника"},
        )
        assert staff_visitor_message.status_code == 200
        assert staff_visitor_message.json()["sender_kind"] == "visitor"
        assert staff.get(
            f"/chat/conversations/{staff_conversation}/messages"
        ).json()[0]["body"] == "Сообщение от пользователя с аккаунтом сотрудника"
        assert other.get(
            f"/chat/conversations/{staff_conversation}/messages"
        ).status_code == 404

        created_conversation = visitor.post("/chat/conversations").json()["conversation"]
        conversation = created_conversation["id"]
        conversation_log = (
            tmp_path / "conversations" / f"{created_conversation['code']}.json"
        )
        assert conversation_log.is_file()
        uploaded = visitor.post(
            f"/chat/conversations/{conversation}/messages",
            data={"body": "Проверьте документ"},
            files={"file": ("document.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        )
        assert uploaded.status_code == 200
        conversation_record = json.loads(conversation_log.read_text(encoding="utf-8"))
        assert conversation_record["messages"][0]["body"] == "Проверьте документ"
        file_info = uploaded.json()["file"]
        assert file_info["media_type"] == "application/pdf"
        file_response = visitor.get(file_info["url"])
        assert file_response.content == b"%PDF-1.4\n%%EOF"
        assert file_response.headers["content-disposition"].startswith("inline;")
        assert other.get(file_info["url"]).status_code == 404
        assert other.get(f"/chat/conversations/{conversation}/messages").status_code == 404

        assert staff.get("/chat/conversations").json()[0]["id"] == conversation
        assert staff.get(f"/chat/conversations/{conversation}/messages").json()[0]["body"] == "Проверьте документ"
        other_conversation = other.post("/chat/conversations").json()["conversation"]["id"]
        with visitor.websocket_connect(f"/chat/ws/{conversation}") as visitor_socket:
            with staff.websocket_connect(f"/chat/ws/{conversation}") as staff_socket:
                with staff.websocket_connect("/chat/staff/ws") as staff_events:
                    visitor_reply = visitor.post(
                        f"/chat/conversations/{conversation}/messages",
                        data={"body": "Ответ посетителя"},
                    )
                    assert visitor_reply.status_code == 200
                    assert staff_socket.receive_json()["message"]["body"] == "Ответ посетителя"
                    assert staff_events.receive_json()["message"]["body"] == "Ответ посетителя"
                    assert visitor_socket.receive_json()["message"]["body"] == "Ответ посетителя"
                    second_reply = other.post(
                        f"/chat/conversations/{other_conversation}/messages",
                        data={"body": "Новое обращение"},
                    )
                    assert second_reply.status_code == 200
                    event = staff_events.receive_json()["message"]
                    assert event["body"] == "Новое обращение"
                    assert event["conversation_id"] == other_conversation
                    staff_reply = staff.post(
                        f"/chat/conversations/{conversation}/messages",
                        data={"body": "Ответ сотрудника", "sender_kind": "staff"},
                    )
                    assert staff_reply.status_code == 200
                    assert staff_reply.json()["sender_kind"] == "staff"
                    assert staff_reply.json()["sender_name"] == registration_email
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

        claimable = store.create_conversation("claimable-visitor")
        claim_response = staff.post(
            f"/chat/conversations/{claimable['id']}/claim"
        )
        assert claim_response.status_code == 200
        assert claim_response.json()["conversation"]["assigned_staff_id"] == store.authenticate(
            registration_email, "a permanent and very secure password"
        )["id"]
        assert claimable["id"] not in {
            item["id"] for item in other.get("/chat/conversations").json()
        }
        assert other.get(
            f"/chat/conversations/{claimable['id']}/messages"
        ).status_code == 404
        assert other.post(
            f"/chat/conversations/{claimable['id']}/messages",
            data={"body": "Чужой ответ", "sender_kind": "staff"},
        ).status_code == 409
        assert staff.get(
            f"/chat/conversations/{claimable['id']}/messages"
        ).status_code == 200
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
