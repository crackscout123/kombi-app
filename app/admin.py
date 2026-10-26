import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func, select

from . import appsettings as S, mail, services as svc
from .auth import send_token_mail
from .db import Session
from .main import DIRECTIONS, STATUS, _int_arg, support_code_hash
from .models import Contact, Entry, SupportGrant, SupportLog, User
from .security import (admin_required, client_ip, hash_password, rate_hit, rate_limited)
from .util import utcnow

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/")
@admin_required
def index():
    return render_template("admin_index.html", stats=svc.system_stats())


# ---------------------------------------------------------------- Nutzer
@bp.get("/users")
@admin_required
def users():
    rows = Session.scalars(select(User).order_by(User.id)).all()
    return render_template("admin_users.html", users=rows)


@bp.post("/users/create")
@admin_required
def user_create():
    name, email = request.form.get("username", "").strip(), request.form.get("email", "").strip().lower()
    role = "admin" if request.form.get("role") == "admin" else "user"
    mode, pw = request.form.get("mode"), request.form.get("password", "")
    if not (2 <= len(name) <= 64) or "@" not in email or len(email) > 255:
        flash("Bitte gültigen Benutzernamen (2–64 Zeichen) und E-Mail angeben.", "error")
        return redirect(url_for("admin.users"))
    if Session.scalar(select(User).where((func.lower(User.username) == name.lower()) | (User.email == email))):
        flash("Benutzername oder E-Mail existiert bereits.", "error")
        return redirect(url_for("admin.users"))
    if mode == "invite":
        u = User(username=name, email=email, role=role)
        Session.add(u)
        Session.commit()
        ok, err = send_token_mail(u, "invite")
        flash("Einladung versendet." if ok else f"Konto angelegt, aber E-Mail nicht versendet ({err}). "
              "Nutze „Einladung erneut senden“, sobald SMTP funktioniert.", "ok" if ok else "error")
    else:
        from .security import check_new_password
        err = check_new_password(pw)
        if err:
            flash(err, "error")
            return redirect(url_for("admin.users"))
        Session.add(User(username=name, email=email, role=role, password_hash=hash_password(pw), must_change_password=True))
        Session.commit()
        flash("Konto angelegt. Das Passwort muss beim ersten Login geändert werden.", "ok")
    return redirect(url_for("admin.users"))


def _other_user(uid):
    u = Session.get(User, uid)
    if not u:
        abort(404)
    if u.id == g.user.id:
        flash("Das eigene Konto kann hier nicht geändert werden.", "error")
        return None
    return u


@bp.post("/users/<int:uid>/toggle")
@admin_required
def user_toggle(uid):
    u = _other_user(uid)
    if u:
        u.active = not u.active
        u.session_version += 1
        Session.commit()
        flash(f"Konto {'entsperrt' if u.active else 'gesperrt'}.", "ok")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:uid>/role")
@admin_required
def user_role(uid):
    u = _other_user(uid)
    if u:
        u.role = "admin" if request.form.get("role") == "admin" else "user"
        Session.commit()
        flash("Rolle geändert.", "ok")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:uid>/reinvite")
@admin_required
def user_reinvite(uid):
    u = Session.get(User, uid) or abort(404)
    if u.password_hash:
        flash("Dieses Konto hat bereits ein Passwort.", "error")
    else:
        ok, err = send_token_mail(u, "invite")
        flash("Einladung versendet." if ok else f"Nicht versendet: {err}", "ok" if ok else "error")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------- Konfiguration
@bp.route("/config", methods=["GET", "POST"])
@admin_required
def config():
    import re
    if request.method == "POST":
        name, color = request.form.get("app_name", "").strip()[:60], request.form.get("accent_color", "")
        if not name or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            flash("Bitte Anwendungsname und Akzentfarbe (#RRGGBB) angeben.", "error")
        else:
            S.set_("app_name", name)
            S.set_("accent_color", color)
            S.set_("registration_enabled", "1" if request.form.get("registration") == "1" else "0")
            Session.commit()
            flash("Konfiguration gespeichert.", "ok")
        return redirect(url_for("admin.config"))
    return render_template("admin_config.html", c={"app_name": S.get("app_name"), "accent": S.get("accent_color"),
                                                    "reg": S.get("registration_enabled") == "1"})


@bp.route("/mail", methods=["GET", "POST"])
@admin_required
def mail_settings():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "smtp":
            try:
                port = int(request.form.get("smtp_port", "587"))
                assert 1 <= port <= 65535
            except (ValueError, AssertionError):
                flash("Ungültiger Port.", "error")
                return redirect(url_for("admin.mail_settings"))
            sec = request.form.get("smtp_security")
            S.set_("smtp_host", request.form.get("smtp_host", "").strip()[:255])
            S.set_("smtp_port", str(port))
            S.set_("smtp_user", request.form.get("smtp_user", "").strip()[:255])
            S.set_("smtp_from", request.form.get("smtp_from", "").strip()[:255])
            S.set_("smtp_security", sec if sec in ("starttls", "ssl", "none") else "starttls")
            if request.form.get("smtp_password"):  # leer = unverändert
                S.set_("smtp_password_enc", mail.encrypt(request.form["smtp_password"]))
            Session.commit()
            flash("SMTP-Einstellungen gespeichert.", "ok")
        elif action == "test":
            ok, err = mail.send(g.user.email, f"Testmail – {S.get('app_name')}", "Die SMTP-Konfiguration funktioniert.")
            flash("Testmail gesendet." if ok else f"Testmail fehlgeschlagen: {err}", "ok" if ok else "error")
        elif action == "templates":
            for k in ("tpl_invite_subject", "tpl_invite_body", "tpl_reset_subject", "tpl_reset_body"):
                v = request.form.get(k, "").strip()
                if v:
                    S.set_(k, v[:5000])
            Session.commit()
            flash("Vorlagen gespeichert.", "ok")
        return redirect(url_for("admin.mail_settings"))
    c = mail.config()
    has_pw = bool(c["password"])
    return render_template("admin_mail.html", c=c, has_pw=has_pw, t={k: S.get(k) for k in (
        "tpl_invite_subject", "tpl_invite_body", "tpl_reset_subject", "tpl_reset_body")})


# ---------------------------------------------------------------- Support-Zugriff
def _support_ctx():
    """Gültige Support-Sitzung oder None. Prüft Widerruf/Ablauf bei jeder Anfrage."""
    s = session.get("support")
    if not s:
        return None
    gr = Session.get(SupportGrant, s["grant"])
    if (not gr or gr.revoked_at or gr.expires_at < utcnow() or gr.user_id != s["uid"] or gr.used_by != g.user.id):
        session.pop("support", None)
        return None
    return gr


@bp.route("/support", methods=["GET", "POST"])
@admin_required
def support():
    if request.method == "POST":
        ident = request.form.get("ident", "").strip().lower()[:255]
        code = "".join(ch for ch in request.form.get("code", "") if ch.isdigit())
        k_admin, k_target = f"support:admin:{g.user.id}", f"support:target:{ident}"
        if rate_limited(k_admin, 5, 15) or rate_limited(k_target, 10, 60):
            abort(429, "Zu viele Fehlversuche. Bitte später erneut versuchen.")
        u = Session.scalar(select(User).where((func.lower(User.username) == ident) | (User.email == ident)))
        gr = None
        if u:
            gr = Session.scalar(select(SupportGrant).where(
                SupportGrant.user_id == u.id, SupportGrant.revoked_at.is_(None), SupportGrant.used_at.is_(None),
                SupportGrant.expires_at > utcnow()).order_by(SupportGrant.id.desc()))
        good = bool(gr) and len(code) == 6 and secrets.compare_digest(gr.code_hash, support_code_hash(u.id, code))
        if not good:
            rate_hit(k_admin); rate_hit(k_target)
            if u:
                Session.add(SupportLog(user_id=u.id, admin_id=g.user.id, admin_name=g.user.username,
                                       event="access_failed", ip=client_ip()))
                Session.commit()
            flash("Nutzer oder Code ungültig, abgelaufen oder bereits verwendet.", "error")
            return redirect(url_for("admin.support"))
        gr.used_at, gr.used_by = utcnow(), g.user.id  # Einmalcode: sofort verbraucht
        Session.add(SupportLog(user_id=u.id, admin_id=g.user.id, admin_name=g.user.username,
                               event="access_ok", ip=client_ip()))
        Session.commit()
        session["support"] = {"grant": gr.id, "uid": u.id}
        return redirect(url_for("admin.support_dashboard"))
    return render_template("admin_support.html", active=_support_ctx())


@bp.get("/support/dashboard")
@admin_required
def support_dashboard():
    gr = _support_ctx()
    if not gr:
        flash("Keine aktive Support-Sitzung.", "error")
        return redirect(url_for("admin.support"))
    direction = request.args.get("richtung") if request.args.get("richtung") in DIRECTIONS else None
    status = request.args.get("status") if request.args.get("status") in STATUS else None
    data = svc.dashboard_data(gr.user_id, direction, status)
    target = Session.get(User, gr.user_id)
    return render_template("support_dashboard.html", **data, target=target, grant=gr, f_dir=direction, f_status=status,
                           STATUS=STATUS, DIRECTIONS=DIRECTIONS, support=True, contacts=[], f_contact=None)


@bp.get("/support/entries/<int:eid>")
@admin_required
def support_entry(eid):
    gr = _support_ctx()
    if not gr:
        abort(403)
    e = Session.get(Entry, eid)
    if not e or e.user_id != gr.user_id:
        abort(404)
    from .main import entry_context
    return render_template("entry_detail.html", support=True, **entry_context(e))


@bp.post("/support/end")
@admin_required
def support_end():
    gr = _support_ctx()
    if gr:
        Session.add(SupportLog(user_id=gr.user_id, admin_id=g.user.id, admin_name=g.user.username,
                               event="access_ended", ip=client_ip()))
        gr.revoked_at = utcnow()
        Session.commit()
    session.pop("support", None)
    flash("Support-Sitzung beendet.", "ok")
    return redirect(url_for("admin.support"))
