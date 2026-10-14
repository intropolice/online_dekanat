from __future__ import annotations

import os
import json
import smtplib
import ssl
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmailDeliveryError(RuntimeError):
    """Raised when email delivery is unavailable or rejected."""


def send_temporary_password(recipient: str, password: str) -> None:
    send_email(
        recipient,
        "Пароль для входа — Онлайн-деканат",
        (
            f"Ваш временный пароль: {password}\n\n"
            "При желании пароль можно изменить в профиле. "
            "Никому не сообщайте пароль. Если вы не создавали аккаунт, "
            "удалите это письмо и сообщите администратору."
        ),
    )


def send_email(recipient: str, subject: str, body: str) -> None:
    brevo_api_key = os.getenv("BREVO_API_KEY", "").strip()
    brevo_sender = os.getenv("BREVO_SENDER_EMAIL", "").strip()
    if brevo_api_key or brevo_sender:
        if not brevo_api_key or not brevo_sender:
            raise EmailDeliveryError(
                "Для Brevo задайте BREVO_API_KEY и BREVO_SENDER_EMAIL"
            )
        _send_via_brevo(recipient, subject, body, brevo_api_key, brevo_sender)
        return

    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    if not host or not sender:
        raise EmailDeliveryError(
            "Почта не настроена: задайте BREVO_API_KEY и BREVO_SENDER_EMAIL "
            "или SMTP_HOST и SMTP_FROM"
        )

    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        timeout = float(os.getenv("SMTP_TIMEOUT_SECONDS", "15"))
    except ValueError as exc:
        raise EmailDeliveryError("Некорректны SMTP_PORT или SMTP_TIMEOUT_SECONDS") from exc
    if not 1 <= port <= 65535 or not 1 <= timeout <= 60:
        raise EmailDeliveryError("Некорректны SMTP_PORT или SMTP_TIMEOUT_SECONDS")

    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        raise EmailDeliveryError("SMTP_USERNAME и SMTP_PASSWORD нужно задать вместе")

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    use_ssl = _env_flag("SMTP_SSL")
    use_starttls = _env_flag("SMTP_STARTTLS", default=not use_ssl)
    if use_ssl and use_starttls:
        raise EmailDeliveryError("Выберите один способ защиты SMTP-соединения")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=ssl.create_default_context()
            ) as client:
                _send(client, message, username, password)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.ehlo()
                if use_starttls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                _send(client, message, username, password)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError(
            "SMTP-сервер отклонил учетные данные. Для Brevo проверьте, что "
            "SMTP_USERNAME — SMTP login из раздела SMTP & API, "
            "SMTP_PASSWORD — SMTP key (не API key), а SMTP_HOST равен "
            "smtp-relay.brevo.com. После изменения настроек пересоздайте "
            "контейнер командой docker compose up -d --force-recreate."
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError(
            f"Не удалось отправить письмо через SMTP ({type(exc).__name__})"
        ) from exc


def _send_via_brevo(
    recipient: str, subject: str, body: str, api_key: str, sender_email: str
) -> None:
    payload = {
        "sender": {
            "name": os.getenv("BREVO_SENDER_NAME", "Онлайн-деканат").strip()
            or "Онлайн-деканат",
            "email": sender_email,
        },
        "to": [{"email": recipient}],
        "subject": subject,
        "textContent": body,
    }
    request = Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(
            request, timeout=float(os.getenv("SMTP_TIMEOUT_SECONDS", "15"))
        ) as response:
            if not 200 <= response.status < 300:
                raise EmailDeliveryError(
                    f"Brevo отклонил отправку письма (HTTP {response.status})"
                )
    except HTTPError as exc:
        raise EmailDeliveryError(
            f"Brevo отклонил отправку письма (HTTP {exc.code})"
        ) from exc
    except (OSError, URLError, TimeoutError) as exc:
        raise EmailDeliveryError(
            f"Не удалось отправить письмо через Brevo ({type(exc).__name__})"
        ) from exc


def _send(client: smtplib.SMTP, message: EmailMessage, username: str, password: str) -> None:
    if username:
        client.login(username, password)
    refused = client.send_message(message)
    if refused:
        raise EmailDeliveryError("SMTP-сервер отклонил адрес получателя")


def _env_flag(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EmailDeliveryError(f"Некорректное логическое значение переменной {name}")
