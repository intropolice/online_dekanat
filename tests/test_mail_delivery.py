from email.message import EmailMessage
import json
import smtplib
from urllib.request import Request

import pytest

from online_dekanat.mail_delivery import EmailDeliveryError, send_email


class FakeSMTP:
    def __init__(self) -> None:
        self.message: EmailMessage | None = None
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, **_kwargs: object) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage) -> dict:
        self.message = message
        return {}


def test_smtp_delivery_uses_configured_starttls_and_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeSMTP()
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "mailer@example.org")
    monkeypatch.setenv("SMTP_PASSWORD", "application-secret")
    monkeypatch.setenv("SMTP_FROM", "dekanat@example.org")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    monkeypatch.setenv("SMTP_SSL", "false")
    monkeypatch.setattr("online_dekanat.mail_delivery.smtplib.SMTP", lambda *_args, **_kwargs: client)

    send_email("employee@adm.rksi.ru", "Код", "Тест")

    assert client.starttls_called
    assert client.login_args == ("mailer@example.org", "application-secret")
    assert client.message is not None
    assert client.message["To"] == "employee@adm.rksi.ru"
    assert client.message.get_content().strip() == "Тест"


def test_smtp_delivery_fails_explicitly_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)

    with pytest.raises(EmailDeliveryError, match="SMTP_HOST"):
        send_email("employee@adm.rksi.ru", "Код", "Тест")


def test_smtp_requires_username_and_password_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_FROM", "dekanat@example.org")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    with pytest.raises(EmailDeliveryError, match="вместе"):
        send_email("employee@adm.rksi.ru", "Код", "Тест")


def test_smtp_authentication_error_has_actionable_brevo_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingSMTP(FakeSMTP):
        def login(self, username: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"authentication failed")

    client = RejectingSMTP()
    monkeypatch.setenv("SMTP_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "smtp-login")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-key")
    monkeypatch.setenv("SMTP_FROM", "sender@example.org")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    monkeypatch.setenv("SMTP_SSL", "false")
    monkeypatch.setattr(
        "online_dekanat.mail_delivery.smtplib.SMTP",
        lambda *_args, **_kwargs: client,
    )

    with pytest.raises(EmailDeliveryError, match="SMTP key \\(не API key\\)") as error:
        send_email("recipient@mail.ru", "Код", "Тест")

    assert "authentication failed" not in str(error.value)


def test_brevo_delivery_uses_api_key_and_verified_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        status = 201

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Request, *, timeout: float) -> Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("BREVO_API_KEY", "test-api-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "verified@example.org")
    monkeypatch.setenv("BREVO_SENDER_NAME", "Тестовый отправитель")
    monkeypatch.setattr("online_dekanat.mail_delivery.urlopen", fake_urlopen)

    send_email("recipient@mail.ru", "Код", "Ваш код: 12345678")

    request = captured["request"]
    assert request.full_url == "https://api.brevo.com/v3/smtp/email"
    assert request.get_header("Api-key") == "test-api-key"
    payload = json.loads(request.data)
    assert payload["sender"] == {
        "name": "Тестовый отправитель",
        "email": "verified@example.org",
    }
    assert payload["to"] == [{"email": "recipient@mail.ru"}]
    assert payload["textContent"] == "Ваш код: 12345678"


def test_brevo_requires_api_key_and_sender_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREVO_API_KEY", "test-api-key")
    monkeypatch.delenv("BREVO_SENDER_EMAIL", raising=False)

    with pytest.raises(EmailDeliveryError, match="BREVO_API_KEY"):
        send_email("recipient@mail.ru", "Код", "Тест")
