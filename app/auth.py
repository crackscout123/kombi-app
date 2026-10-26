import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from . import appsettings as S, mail
from .db import Session
from .models import Token, User
from .security import (check_new_password, client_ip, hash_password, rate_hit, rate_limited, sha256, verify_password)
from .util import utcnow

bp = Blueprint("auth", __name__)


def make_token(user, kind, hours):
    raw = secrets.token_urlsafe(32)
    for t in Session.scalars(select(Token).where(Token.user_id == user.id, Token.kind == kind, Token.used_at.is_(None))):
        t.used_at = utcnow()
    Session.add(Token(user_id=user.id, kind=kind, token_hash=sha256(raw), expires_at=utcnow() + timedelta(hours=hours)))
    Session.commit()
    return raw


def link_for(endpoint, token):
    base = current_app.config.get("BASE_URL", "").rstrip("/")
    path = url_for(endpoint, token=token)
    return (base + path) if base else url_for(endpoint, token=token, _external=True)


def send_token_mail(user, kind):
    """kind: invite | reset. Gibt (ok, fehler) zurück; der Link wird nie angezeigt."""
    raw = make_token(user, kind, 72 if kind == "invite" else 2)
    link = link_for("auth.invite" if kind == "invite" else "auth.reset", raw)
    ctx = dict(name=user.username, link=link, app=S.get("app_name"))
    subj = S.render_template_text(S.get(f"tpl_{kind}_subject"), **ctx)
    body = S.render_template_text(S.get(f"tpl_{kind}_body"), **ctx)
    return mail.send(user.email, subj, body)


def _valid_token(raw, kind):
    t = Session.scalar(select(Token).where(Token.token_hash == sha256(raw or ""), Token.kind == kind))
    if not t or t.used_at or t.expires_at < utcnow():
        return None
    return t


def start_session(user):
    session.clear()
    session.permanent = True
    session["uid"], session["sv"] = user.id, user.session_version
    user.last_login_at = utcnow()
    Session.commit()


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        ident = request.form.get("login", "").strip().lower()[:255]
        k_ip, k_u = f"login:ip:{client_ip()}", f"login:u:{ident}"
        if rate_limited(k_ip, 20, 15) or rate_limited(k_u, 8, 15):
            abort(429, "Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen.")
        u = Session.scalar(select(User).where((func.lower(User.username) == ident) | (func.lower(User.email) == ident)))
        ok = verify_password(u.password_hash if u else None, request.form.get("password", ""))
        if u and ok and u.active:
            start_session(u)
            return redirect(url_for("main.dashboard"))
        rate_hit(k_ip); rate_hit(k_u)
        flash("Anmeldung fehlgeschlagen oder Konto gesperrt.", "error")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if S.get("registration_enabled") != "1":
        abort(404)
    if request.method == "POST":
        name, email = request.form.get("username", "").strip(), request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        err = check_new_password(pw, request.form.get("password2", ""))
        if rate_limited(f"reg:{client_ip()}", 5, 60):
            abort(429, "Zu viele Registrierungen von dieser Adresse.")
        if not (2 <= len(name) <= 64) or "@" not in email or len(email) > 255:
            err = "Bitte gültigen Benutzernamen (2–64 Zeichen) und E-Mail angeben."
        elif Session.scalar(select(User).where((func.lower(User.username) == name.lower()) | (User.email == email))):
            err = "Benutzername oder E-Mail ist bereits vergeben."
        rate_hit(f"reg:{client_ip()}")
        if err:
            flash(err, "error")
        else:
            Session.add(User(username=name, email=email, password_hash=hash_password(pw)))
            Session.commit()
            flash("Konto angelegt. Bitte anmelden.", "ok")
            return redirect(url_for("auth.login"))
    return render_template("register.html")


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        if rate_limited(f"forgot:{client_ip()}", 5, 60):
            abort(429, "Zu viele Anfragen. Bitte später erneut versuchen.")
        rate_hit(f"forgot:{client_ip()}")
        email = request.form.get("email", "").strip().lower()
        u = Session.scalar(select(User).where(User.email == email))
        if u and u.active and u.password_hash:
            send_token_mail(u, "reset")
        flash("Falls ein Konto mit dieser E-Mail existiert, wurde ein Link gesendet.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("forgot.html")


def _set_password_view(kind, token, title):
    t = _valid_token(token, kind)
    if not t:
        flash("Der Link ist ungültig oder abgelaufen.", "error")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        err = check_new_password(pw, request.form.get("password2", ""))
        if err:
            flash(err, "error")
        else:
            u = Session.get(User, t.user_id)
            u.password_hash, u.must_change_password = hash_password(pw), False
            u.session_version += 1
            t.used_at = utcnow()
            Session.commit()
            flash("Passwort gespeichert. Bitte anmelden.", "ok")
            return redirect(url_for("auth.login"))
    return render_template("set_password.html", title=title)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    return _set_password_view("reset", token, "Neues Passwort setzen")


@bp.route("/invite/<token>", methods=["GET", "POST"])
def invite(token):
    return _set_password_view("invite", token, "Einladung annehmen – Passwort festlegen")
