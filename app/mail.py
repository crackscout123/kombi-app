import base64
import hashlib
import smtplib
from email.message import EmailMessage
from email.utils import formatdate

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from . import appsettings as S, util


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(current_app.config["SECRET_KEY"].encode()).digest())
    return Fernet(key)


def encrypt(plain):
    return _fernet().encrypt(plain.encode()).decode() if plain else ""


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode() if token else ""
    except InvalidToken:
        return ""


def config():
    return {
        "host": S.get("smtp_host") or util.env("SMTP_HOST", ""),
        "port": int(S.get("smtp_port") or util.env("SMTP_PORT", "587")),
        "user": S.get("smtp_user") or util.env("SMTP_USER", ""),
        "password": decrypt(S.get("smtp_password_enc")) or util.env("SMTP_PASSWORD", ""),
        "sender": S.get("smtp_from") or util.env("SMTP_FROM", "") or "kombi@localhost",
        "security": S.get("smtp_security") or "starttls",
    }


def send(to, subject, body):
    """Gibt (ok, fehlertext) zurück. Wirft nie."""
    c = config()
    if not c["host"]:
        return False, "SMTP ist nicht konfiguriert."
    msg = EmailMessage()
    msg["From"], msg["To"] = c["sender"].replace("\n", " "), to.replace("\n", " ")
    msg["Subject"] = subject.replace("\n", " ").replace("\r", " ")
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    try:
        if c["security"] == "ssl":
            srv = smtplib.SMTP_SSL(c["host"], c["port"], timeout=10)
        else:
            srv = smtplib.SMTP(c["host"], c["port"], timeout=10)
            if c["security"] == "starttls":
                srv.starttls()
        with srv:
            if c["user"]:
                srv.login(c["user"], c["password"])
            srv.send_message(msg)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("SMTP-Fehler: %s", exc)
        return False, f"Versand fehlgeschlagen: {exc.__class__.__name__}"
