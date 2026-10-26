from .db import Session
from .models import Setting
from . import util

DEFAULTS = {
    "app_name": "Kombi",
    "accent_color": "#2563eb",
    "registration_enabled": "0",
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_from": "",
    "smtp_security": "starttls", "smtp_password_enc": "",
    "tpl_invite_subject": "Einladung zu {app}",
    "tpl_invite_body": "Hallo {name},\n\nDu wurdest zu {app} eingeladen. Lege hier dein Passwort fest (Link 72 Stunden gültig):\n{link}\n\nWenn du die Einladung nicht erwartest, ignoriere diese E-Mail.",
    "tpl_reset_subject": "Passwort zurücksetzen – {app}",
    "tpl_reset_body": "Hallo {name},\n\nüber diesen Link kannst du ein neues Passwort setzen (2 Stunden gültig):\n{link}\n\nWenn du das nicht angefordert hast, ignoriere diese E-Mail.",
}


def get(key):
    row = Session.get(Setting, key)
    if row is not None:
        return row.value
    if key == "app_name":
        return util.env("APP_NAME", DEFAULTS[key])
    return DEFAULTS.get(key, "")


def set_(key, value):
    row = Session.get(Setting, key)
    if row is None:
        Session.add(Setting(key=key, value=value))
    else:
        row.value = value


def render_template_text(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace("{" + k + "}", str(v))
    return tpl
