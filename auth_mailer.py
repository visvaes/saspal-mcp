import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlencode


def is_configured() -> bool:
    return all(
        os.getenv(name)
        for name in ("SMTP_HOST", "SMTP_FROM_EMAIL", "APP_BASE_URL")
    )


def send_password_reset_email(recipient: str, token: str) -> None:
    if not is_configured():
        raise RuntimeError("Password recovery email is not configured.")

    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.environ["SMTP_FROM_EMAIL"]
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    reset_url = f"{base_url}/reset-password#{urlencode({'token': token})}"
    message = EmailMessage()
    message["Subject"] = "Reset your SASPAL Gmail Assistant password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your SASPAL Gmail Assistant account.\n\n"
        f"Use this one-time link within 30 minutes: {reset_url}\n\n"
        "If you did not request a reset, you can ignore this email."
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        username = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        if username and password:
            client.login(username, password)
        client.send_message(message)
