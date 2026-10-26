import logging
import re
import time

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL
from werkzeug.middleware.proxy_fix import ProxyFix

from . import appsettings as S, migrations, util
from .db import Session
from .models import Contact, Entry, Payment, User, WeekOverride
from .security import csrf_token, hash_password, verify_csrf

log = logging.getLogger("kombi")
SAFE_ENDPOINTS = {"static", "health", "theme", "auth.logout", "main.settings", "main.change_password"}


def _db_url(cfg):
    url = cfg("DATABASE_URL")
    if url:
        return url
    return URL.create("postgresql+psycopg2", username=cfg("DB_USER", "kombi"), password=cfg("DB_PASSWORD", ""),
                      host=cfg("DB_HOST", "db"), port=int(cfg("DB_PORT", "5432")), database=cfg("DB_NAME", "kombi"))


def _wait_for_db(engine, tries, delay):
    for i in range(1, tries + 1):
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("Datenbank nicht erreichbar (%s/%s): %s", i, tries, exc.__class__.__name__)
            if i == tries:
                raise
            time.sleep(delay)


def _seed_admin(cfg):
    if Session.scalar(select(User).where(User.role == "admin")):
        return
    name, email, pw = cfg("ADMIN_USERNAME", "admin"), cfg("ADMIN_EMAIL"), cfg("ADMIN_PASSWORD")
    if not (email and pw):
        log.warning("Kein Admin vorhanden und ADMIN_EMAIL/ADMIN_PASSWORD nicht gesetzt.")
        return
    Session.add(User(username=name, email=email.lower(), password_hash=hash_password(pw), role="admin"))
    Session.commit()
    log.info("Erster Admin '%s' angelegt.", name)


def create_app(config=None):
    config = config or {}

    def cfg(key, default=None):
        return config[key] if key in config else util.env(key, default)

    app = Flask(__name__)
    secret = cfg("SECRET_KEY")
    if not secret or len(secret) < 16:
        raise RuntimeError("SECRET_KEY (bzw. SECRET_KEY_FILE) fehlt oder ist zu kurz (min. 16 Zeichen).")
    secure = config["COOKIE_SECURE"] if "COOKIE_SECURE" in config else util.env_bool("COOKIE_SECURE", True)
    app.config.update(
        SECRET_KEY=secret, SESSION_COOKIE_SECURE=secure, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_NAME="kombi_session",
        PERMANENT_SESSION_LIFETIME=8 * 3600, MAX_CONTENT_LENGTH=256 * 1024,
        BASE_URL=cfg("BASE_URL", ""), TESTING=bool(config.get("TESTING")))
    if util.env_bool("TRUST_PROXY", True):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    logging.basicConfig(level=logging.INFO)

    engine = create_engine(_db_url(cfg), pool_pre_ping=True)
    _wait_for_db(engine, int(cfg("DB_RETRIES", "60")), float(cfg("DB_RETRY_DELAY", "2")))
    migrations.run(engine)
    Session.remove()
    Session.configure(bind=engine)
    _seed_admin(cfg)
    if (config["SEED_DEMO"] if "SEED_DEMO" in config else util.env_bool("SEED_DEMO", False)):
        from .seed import seed_demo
        seed_demo(cfg("DEMO_PASSWORD", "demo-passwort-123"))
    Session.remove()
    if engine.dialect.name == "postgresql":
        engine.dispose()  # vor dem Forken (gunicorn --preload)

    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .admin import bp as admin_bp
    for b in (auth_bp, main_bp, admin_bp):
        app.register_blueprint(b)

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            u = Session.get(User, uid)
            if u and u.active and u.password_hash and session.get("sv") == u.session_version:
                g.user = u
            else:
                session.clear()
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            verify_csrf()
        if (g.user and g.user.must_change_password and request.endpoint not in SAFE_ENDPOINTS):
            return redirect(url_for("main.settings"))

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if request.is_secure:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.teardown_appcontext
    def cleanup(exc):
        Session.remove()

    @app.context_processor
    def inject():
        return {"app_name": S.get("app_name"), "csrf_token": csrf_token, "me": g.get("user")}

    app.add_template_filter(util.fmt_eur, "eur")
    app.add_template_filter(util.fmt_date, "ddate")
    app.add_template_filter(util.fmt_dt, "dtime")

    @app.get("/health")
    def health():
        try:
            Session.execute(text("SELECT 1"))
            return jsonify(status="ok")
        except Exception:  # noqa: BLE001
            return jsonify(status="db-error"), 503

    @app.get("/theme.css")
    def theme():
        color = S.get("accent_color")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
            color = "#2563eb"
        return Response(f":root{{--accent:{color};}}", mimetype="text/css")

    for code, msg in ((400, "Ungültige Anfrage"), (403, "Kein Zugriff"), (404, "Nicht gefunden"),
                      (405, "Methode nicht erlaubt"), (429, "Zu viele Versuche"), (500, "Interner Fehler")):
        def handler(e, code=code, msg=msg):
            desc = getattr(e, "description", "") if code in (400, 429) else ""
            return render_template("error.html", code=code, msg=msg, desc=desc), code
        app.register_error_handler(code, handler)
    return app
