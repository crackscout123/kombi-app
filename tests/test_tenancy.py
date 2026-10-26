import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import create_app
from app.db import Session
from app.models import Contact, Entry, SupportGrant, User
from app.security import hash_password
from app.util import today_berlin


@pytest.fixture()
def app(tmp_path):
    a = create_app({"DATABASE_URL": f"sqlite:///{tmp_path}/t.db", "SECRET_KEY": "test-secret-key-0123456789",
                    "COOKIE_SECURE": False, "TESTING": True, "ADMIN_EMAIL": "root@example.invalid",
                    "ADMIN_PASSWORD": "admin-password-1", "ADMIN_USERNAME": "root", "SEED_DEMO": False})
    with a.app_context():
        for n in ("alice", "bob"):
            u = User(username=n, email=f"{n}@example.invalid", password_hash=hash_password(f"{n}-password-1"))
            Session.add(u)
            Session.flush()
            c = Contact(user_id=u.id, first_name=f"Kontakt-von-{n}")
            Session.add(c)
            Session.flush()
            Session.add(Entry(user_id=u.id, contact_id=c.id, direction="give", category="money",
                              loan_date=today_berlin() - timedelta(days=9), principal_cents=10000, rate_cents=500))
        Session.commit()
    return a


def login(app, name, pw=None):
    c = app.test_client()
    c.get("/login")
    r = post(c, "/login", {"login": name, "password": pw or f"{name}-password-1"})
    assert r.status_code == 302, r.data
    return c


def post(c, url, data=None):
    with c.session_transaction() as s:
        s.setdefault("_csrf", "tok")
        tok = s["_csrf"]
    return c.post(url, data={**(data or {}), "csrf_token": tok})


def ids(app, name):
    with app.app_context():
        u = Session.scalar(select(User).where(User.username == name))
        e = Session.scalar(select(Entry).where(Entry.user_id == u.id))
        return u.id, e.id, e.contact_id


def test_csrf_required(app):
    c = login(app, "alice")
    assert c.post("/contacts/new", data={"first_name": "X"}).status_code == 400


def test_user_cannot_access_foreign_entry(app):
    _, eid_a, cid_a = ids(app, "alice")
    c = login(app, "bob")
    assert c.get(f"/entries/{eid_a}").status_code == 404
    assert c.get(f"/entries/{eid_a}/edit").status_code == 404
    assert post(c, f"/entries/{eid_a}/delete").status_code == 404
    assert post(c, f"/entries/{eid_a}/payments/new", {"kind": "money", "amount": "5", "paid_on": "01.01.2020"}).status_code == 404
    assert c.get(f"/entries/{eid_a}/payments/preview?kind=money&amount=5&paid_on=01.01.2020").status_code == 404
    assert post(c, f"/contacts/{cid_a}/delete").status_code == 404
    with app.app_context():
        assert Session.get(Entry, eid_a) is not None


def test_cannot_use_foreign_contact(app):
    _, _, cid_a = ids(app, "alice")
    c = login(app, "bob")
    r = post(c, "/entries/new", {"direction": "give", "category": "money", "contact_id": cid_a,
                                 "loan_date": "01.01.2026", "amount": "10", "rate": "0", "cap_type": "none"})
    assert r.status_code == 400


def test_dashboard_lists_only_own(app):
    c = login(app, "bob")
    html = c.get("/").get_data(as_text=True)
    assert "Kontakt-von-bob" in html and "Kontakt-von-alice" not in html


def test_non_admin_has_no_admin_access(app):
    c = login(app, "alice")
    for url in ("/admin/", "/admin/users", "/admin/support", "/admin/mail", "/admin/config"):
        assert c.get(url).status_code == 403


def test_admin_sees_no_names_or_details(app):
    c = login(app, "root", "admin-password-1")
    for url in ("/admin/", "/admin/users", "/admin/support", "/admin/config"):
        html = c.get(url).get_data(as_text=True)
        assert "Kontakt-von-" not in html
    _, eid_a, _ = ids(app, "alice")
    assert c.get(f"/entries/{eid_a}").status_code == 404
    assert c.get(f"/admin/support/entries/{eid_a}").status_code == 403


def make_code(app, name="alice"):
    c = login(app, name)
    html = post(c, "/settings/support/create").get_data(as_text=True)
    return re.search(r'class="code">(\d{6})<', html).group(1)


def test_support_flow(app):
    code = make_code(app)
    admin = login(app, "root", "admin-password-1")
    assert post(admin, "/admin/support", {"ident": "alice", "code": "000000" if code != "000000" else "111111"}).status_code == 302
    assert admin.get("/admin/support/dashboard").status_code == 302  # noch keine Sitzung
    r = post(admin, "/admin/support", {"ident": "alice@example.invalid", "code": code})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/support/dashboard")
    html = admin.get("/admin/support/dashboard").get_data(as_text=True)
    assert "Kontakt-von-alice" in html and "Schreibgeschützt" in html
    _, eid_a, _ = ids(app, "alice")
    _, eid_b, _ = ids(app, "bob")
    assert admin.get(f"/admin/support/entries/{eid_a}").status_code == 200
    assert admin.get(f"/admin/support/entries/{eid_b}").status_code == 404
    assert post(admin, f"/entries/{eid_a}/delete").status_code == 404  # keine Schreibrechte
    other = login(app, "root", "admin-password-1")  # Code ist einmalig
    assert post(other, "/admin/support", {"ident": "alice", "code": code}).headers["Location"].endswith("/admin/support")
    alice = login(app, "alice")
    assert "Support-Zugriff gestartet" in alice.get("/settings").get_data(as_text=True)


def test_support_revoke_ends_session(app):
    code = make_code(app)
    admin = login(app, "root", "admin-password-1")
    post(admin, "/admin/support", {"ident": "alice", "code": code})
    alice = login(app, "alice")
    post(alice, "/settings/support/revoke")
    assert admin.get("/admin/support/dashboard").status_code == 302


def test_support_expired_code(app):
    code = make_code(app)
    with app.app_context():
        g = Session.scalar(select(SupportGrant))
        g.expires_at = g.created_at - timedelta(hours=1)
        Session.commit()
    admin = login(app, "root", "admin-password-1")
    r = post(admin, "/admin/support", {"ident": "alice", "code": code})
    assert r.headers["Location"].endswith("/admin/support")
    assert admin.get("/admin/support/dashboard").status_code == 302


def test_support_rate_limit(app):
    make_code(app)
    admin = login(app, "root", "admin-password-1")
    for _ in range(5):
        assert post(admin, "/admin/support", {"ident": "alice", "code": "123456"}).status_code == 302
    assert post(admin, "/admin/support", {"ident": "alice", "code": "123456"}).status_code == 429


def test_registration_off_by_default(app):
    assert app.test_client().get("/register").status_code == 404


def test_blocked_user_loses_session(app):
    c = login(app, "alice")
    admin = login(app, "root", "admin-password-1")
    uid, _, _ = ids(app, "alice")
    post(admin, f"/admin/users/{uid}/toggle")
    assert c.get("/").status_code == 302


def test_health(app):
    assert app.test_client().get("/health").get_json() == {"status": "ok"}
