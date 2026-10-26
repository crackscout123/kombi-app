import hashlib
import hmac
import secrets
from datetime import timedelta
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import abort, flash, g, redirect, request, session, url_for
from sqlalchemy import delete, func, select

from .db import Session
from .models import RateLimit
from .util import utcnow

_ph = PasswordHasher()
_DUMMY = _ph.hash("dummy-password-for-timing")
MIN_PW = 10


def hash_password(pw):
    return _ph.hash(pw)


def verify_password(stored, pw):
    try:
        return _ph.verify(stored or _DUMMY, pw) and bool(stored)
    except (VerificationError, InvalidHashError):
        return False


def check_new_password(pw, pw2=None):
    if len(pw or "") < MIN_PW:
        return f"Das Passwort muss mindestens {MIN_PW} Zeichen lang sein."
    if pw2 is not None and pw != pw2:
        return "Die Passwörter stimmen nicht überein."
    return None


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def verify_csrf():
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    good = session.get("_csrf", "")
    if not good or not hmac.compare_digest(sent, good):
        abort(400, "Ungültiges oder fehlendes CSRF-Token. Bitte Seite neu laden.")


def login_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return f(*a, **kw)
    return inner


def admin_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login"))
        if not g.user.is_admin:
            abort(403)
        return f(*a, **kw)
    return inner


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def rate_limited(key, limit, window_minutes):
    key = key[:190]
    since = utcnow() - timedelta(minutes=window_minutes)
    n = Session.scalar(select(func.count()).select_from(RateLimit)
                       .where(RateLimit.key == key, RateLimit.created_at > since))
    return n >= limit


def rate_hit(key):
    key = key[:190]
    Session.execute(delete(RateLimit).where(RateLimit.created_at < utcnow() - timedelta(days=1)))
    Session.add(RateLimit(key=key))
    Session.commit()


def client_ip():
    return request.remote_addr or "?"
