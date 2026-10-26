import re
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import select

from . import engine as eng, services as svc, util
from .db import Session
from .models import Contact, Entry, Payment, SupportGrant, SupportLog, WeekOverride
from .security import (check_new_password, client_ip, hash_password, login_required, sha256, verify_password)
from .util import parse_date, parse_eur, parse_int, today_berlin, utcnow

bp = Blueprint("main", __name__)
RELATIONS = {"friend": "Freund", "family": "Familie", "work": "Arbeit", "other": "Anderes"}
STATUS = {"open": "Offen", "partial": "Teilbeglichen", "done": "Erledigt"}
DIRECTIONS = {"give": "Verliehen (Forderung)", "take": "Geliehen (Verbindlichkeit)"}


def support_code_hash(user_id, code):
    from flask import current_app
    return sha256(f"{current_app.config['SECRET_KEY']}:{user_id}:{code}")


def own_entry(eid):
    e = Session.get(Entry, eid)
    if not e or e.user_id != g.user.id:
        abort(404)
    return e


def own_payment(pid):
    p = Session.get(Payment, pid)
    if not p:
        abort(404)
    e = Session.get(Entry, p.entry_id)
    if not e or e.user_id != g.user.id:
        abort(404)
    return p, e


def own_contact(cid):
    c = Session.get(Contact, cid)
    if not c or c.user_id != g.user.id:
        abort(404)
    return c


def _int_arg(name):
    try:
        return int(request.args.get(name, ""))
    except ValueError:
        return None


# ---------------------------------------------------------------- Dashboard
@bp.get("/")
@login_required
def dashboard():
    direction = request.args.get("richtung") if request.args.get("richtung") in DIRECTIONS else None
    status = request.args.get("status") if request.args.get("status") in STATUS else None
    cid = _int_arg("kontakt")
    data = svc.dashboard_data(g.user.id, direction, status, cid)
    contacts = Session.scalars(select(Contact).where(Contact.user_id == g.user.id).order_by(Contact.first_name)).all()
    return render_template("dashboard.html", **data, contacts=contacts, f_dir=direction, f_status=status,
                           f_contact=cid, STATUS=STATUS, DIRECTIONS=DIRECTIONS, support=False)


# ---------------------------------------------------------------- Einträge
OVR_RE = re.compile(r"^\s*(\d+)\s*[=:]\s*(.+?)\s*$")


def parse_overrides(text):
    out = {}
    for line in re.split(r"[\n;]+", text or ""):
        if not line.strip():
            continue
        m = OVR_RE.match(line)
        if not m:
            raise ValueError(f"Überschreibung „{line.strip()}“ ungültig. Format: Woche=Betrag, z. B. 2=8,00")
        wk = parse_int(m.group(1), 1, 1040, "Woche")
        out[wk] = parse_eur(m.group(2))
    return out


def rate_inputs(form):
    """Gemeinsam für Formular und Vorschau. Gibt dict mit EntryInput-Teilen zurück."""
    rate = parse_eur(form.get("rate", "0") or "0")
    ct = form.get("cap_type", "none")
    if ct not in ("none", "amount", "weeks"):
        raise ValueError("Ungültige Deckel-Art.")
    cv = 0
    if ct == "amount":
        cv = parse_eur(form.get("cap_value", ""))
    elif ct == "weeks":
        cv = parse_int(form.get("cap_value", ""), 1, 1040, "Deckel (Wochen)")
    return rate, parse_overrides(form.get("overrides", "")), ct, cv


def parse_entry_form(form):
    errors, d = [], {}

    def attempt(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ValueError as exc:
            errors.append(str(exc))

    d["direction"] = form.get("direction")
    if d["direction"] not in DIRECTIONS:
        errors.append("Bitte eine Richtung wählen.")
    d["category"] = form.get("category")
    if d["category"] not in ("money", "item"):
        errors.append("Bitte eine Kategorie wählen.")
    cid = attempt(parse_int, form.get("contact_id"), 1, 10 ** 12, "Kontakt")
    c = Session.get(Contact, cid) if cid else None
    if not c or c.user_id != g.user.id:
        errors.append("Bitte einen Kontakt aus deinem Adressbuch wählen.")
    else:
        d["contact_id"] = c.id
    d["loan_date"] = attempt(parse_date, form.get("loan_date"))
    d["note"] = (form.get("note") or "").strip()[:2000] or None
    d["title"] = (form.get("title") or "").strip()[:120] or None
    d["quantity"] = None
    if d["category"] == "item":
        if not d["title"]:
            errors.append("Bitte eine Objektbezeichnung angeben.")
        d["quantity"] = attempt(parse_int, form.get("quantity"), 1, 100000, "Stückzahl (g)")
    d["principal_cents"] = attempt(parse_eur, form.get("amount"))
    if d["principal_cents"] is not None and d["principal_cents"] <= 0:
        errors.append("Der Betrag bzw. Gesamtwert muss größer als 0 sein.")
    try:
        d["rate_cents"], d["overrides"], d["cap_type"], d["cap_value"] = rate_inputs(form)
    except ValueError as exc:
        errors.append(str(exc))
    d["exclude_from_total"] = form.get("exclude") == "1"
    return d, errors


def apply_entry(e, d):
    for k in ("direction", "category", "contact_id", "loan_date", "note", "title", "quantity", "principal_cents",
              "rate_cents", "cap_type", "cap_value", "exclude_from_total"):
        setattr(e, k, d[k])
    if e.category == "money":
        e.quantity = None
    e.overrides.clear()
    Session.flush()  # erst löschen, dann neu anlegen (Unique-Constraint)
    for wk, cents in d["overrides"].items():
        e.overrides.append(WeekOverride(week_no=wk, rate_cents=cents))


def form_values_from_entry(e):
    ov = "\n".join(f"{o.week_no}={util.fmt_eur(o.rate_cents)[:-2]}" for o in sorted(e.overrides, key=lambda o: o.week_no))
    return {"direction": e.direction, "category": e.category, "contact_id": e.contact_id,
            "loan_date": util.fmt_date(e.loan_date), "title": e.title or "", "quantity": e.quantity or "",
            "amount": util.fmt_eur(e.principal_cents)[:-2], "note": e.note or "",
            "rate": util.fmt_eur(e.rate_cents)[:-2], "cap_type": e.cap_type,
            "cap_value": (util.fmt_eur(e.cap_value)[:-2] if e.cap_type == "amount" else (e.cap_value if e.cap_type == "weeks" else "")),
            "exclude": "1" if e.exclude_from_total else "", "overrides": ov}


def _contacts():
    return Session.scalars(select(Contact).where(Contact.user_id == g.user.id).order_by(Contact.first_name)).all()


@bp.route("/entries/new", methods=["GET", "POST"])
@login_required
def entry_new():
    contacts = _contacts()
    if request.method == "POST":
        d, errors = parse_entry_form(request.form)
        if errors:
            for m in errors:
                flash(m, "error")
            return render_template("entry_form.html", f=request.form, contacts=contacts, edit=False), 400
        e = Entry(user_id=g.user.id)
        apply_entry(e, d)
        Session.add(e)
        Session.commit()
        flash("Eintrag angelegt.", "ok")
        return redirect(url_for("main.entry_detail", eid=e.id))
    if not contacts:
        flash("Lege zuerst einen Kontakt an.", "error")
        return redirect(url_for("main.contact_new"))
    f = {"direction": request.args.get("richtung", "give"), "category": "money", "loan_date": util.fmt_date(today_berlin()),
         "rate": "0,00", "cap_type": "none", "contact_id": request.args.get("kontakt", "")}
    return render_template("entry_form.html", f=f, contacts=contacts, edit=False)


@bp.route("/entries/<int:eid>/edit", methods=["GET", "POST"])
@login_required
def entry_edit(eid):
    e = own_entry(eid)
    contacts = _contacts()
    if request.method == "POST":
        d, errors = parse_entry_form(request.form)
        returned = sum(p.units for p in e.payments if p.kind == "return")
        if d.get("category") == "money" and returned:
            errors.append("Kategorie kann nicht auf Geld geändert werden, solange Rückgaben existieren.")
        if d.get("category") == "item" and d.get("quantity") and d["quantity"] < returned:
            errors.append(f"Stückzahl darf nicht unter den bereits zurückgegebenen {returned}g liegen.")
        if errors:
            for m in errors:
                flash(m, "error")
            return render_template("entry_form.html", f=request.form, contacts=contacts, edit=True, entry=e), 400
        apply_entry(e, d)
        Session.commit()
        flash("Eintrag gespeichert. Der Saldo wurde neu berechnet.", "ok")
        return redirect(url_for("main.entry_detail", eid=e.id))
    return render_template("entry_form.html", f=form_values_from_entry(e), contacts=contacts, edit=True, entry=e)


@bp.post("/entries/<int:eid>/delete")
@login_required
def entry_delete(eid):
    e = own_entry(eid)
    Session.delete(e)
    Session.commit()
    flash("Eintrag gelöscht.", "ok")
    return redirect(url_for("main.dashboard"))


def entry_context(e):
    ei, pays = svc.to_inputs(e)
    r = eng.compute(ei, pays, today_berlin())
    weeks = min(r.weeks, 104)
    return {"e": e, "r": r, "schedule": eng.schedule(ei, weeks), "sched_truncated": r.weeks > 104,
            "STATUS": STATUS, "DIRECTIONS": DIRECTIONS}


@bp.get("/entries/<int:eid>")
@login_required
def entry_detail(eid):
    return render_template("entry_detail.html", support=False, **entry_context(own_entry(eid)))


@bp.get("/entries/preview")
@login_required
def entry_preview():
    try:
        rate, ov, ct, cv = rate_inputs(request.args)
    except ValueError as exc:
        return jsonify(error=str(exc))
    ei = eng.EntryInput(today_berlin(), 0, None, rate, ov, ct, cv)
    marks = [1, 4, 8, 12]
    if ct == "weeks":
        marks.append(cv)
    rows = [{"weeks": w_, "text": util.fmt_eur(eng.surcharge_for_weeks(ei, w_))} for w_ in sorted(set(marks)) if w_ <= 1040]
    cap = {"none": "Kein Deckel", "amount": f"Deckel bei {util.fmt_eur(cv)}", "weeks": f"Deckel nach {cv} Wochen"}[ct]
    return jsonify(rows=rows, cap=cap, rate=util.fmt_eur(rate))


# ---------------------------------------------------------------- Tilgung
def parse_payment_form(form, e, skip_id=None):
    kind = form.get("kind", "money")
    if kind not in ("money", "return") or (kind == "return" and e.category != "item"):
        raise ValueError("Ungültige Tilgungsart.")
    paid_on = parse_date(form.get("paid_on"))
    if paid_on > today_berlin():
        raise ValueError("Das Datum darf nicht in der Zukunft liegen.")
    note = (form.get("note") or "").strip()[:1000] or None
    if kind == "money":
        amt = parse_eur(form.get("amount"))
        if amt <= 0:
            raise ValueError("Die Zahlung muss größer als 0 sein.")
        order = form.get("order", "surcharge_first")
        if order not in ("surcharge_first", "principal_first"):
            raise ValueError("Ungültige Verrechnungsreihenfolge.")
        return dict(kind=kind, amount_cents=amt, units=0, order_mode=order, paid_on=paid_on, note=note)
    already = sum(p.units for p in e.payments if p.kind == "return" and p.id != skip_id)
    left = (e.quantity or 0) - already
    units = parse_int(form.get("units"), 1, max(left, 1), "Stückzahl")
    if units > left:
        raise ValueError(f"Es sind nur noch {left}g offen.")
    return dict(kind=kind, amount_cents=0, units=units, order_mode="surcharge_first", paid_on=paid_on, note=note)


@bp.route("/entries/<int:eid>/payments/new", methods=["GET", "POST"])
@login_required
def payment_new(eid):
    e = own_entry(eid)
    if request.method == "POST":
        try:
            vals = parse_payment_form(request.form, e)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("payment_form.html", e=e, f=request.form, edit=False, pid=None), 400
        e.payments.append(Payment(**vals))
        Session.commit()
        flash("Tilgung gespeichert.", "ok")
        return redirect(url_for("main.entry_detail", eid=e.id))
    r = svc.evaluate(e)
    f = {"kind": "money", "paid_on": util.fmt_date(today_berlin()), "order": "surcharge_first",
         "amount": util.fmt_eur(r.saldo)[:-2] if r.saldo else ""}
    return render_template("payment_form.html", e=e, f=f, edit=False, pid=None, r=r)


@bp.route("/payments/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def payment_edit(pid):
    p, e = own_payment(pid)
    if request.method == "POST":
        try:
            vals = parse_payment_form(request.form, e, skip_id=p.id)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("payment_form.html", e=e, f=request.form, edit=True, pid=p.id, r=svc.evaluate(e)), 400
        for k, v in vals.items():
            setattr(p, k, v)
        Session.commit()
        flash("Tilgung geändert. Der Saldo wurde neu berechnet.", "ok")
        return redirect(url_for("main.entry_detail", eid=e.id))
    f = {"kind": p.kind, "paid_on": util.fmt_date(p.paid_on), "order": p.order_mode, "note": p.note or "",
         "amount": util.fmt_eur(p.amount_cents)[:-2] if p.kind == "money" else "", "units": p.units or ""}
    return render_template("payment_form.html", e=e, f=f, edit=True, pid=p.id, r=svc.evaluate(e))


@bp.post("/payments/<int:pid>/delete")
@login_required
def payment_delete(pid):
    p, e = own_payment(pid)
    e.payments.remove(p)
    Session.commit()
    flash("Tilgung gelöscht. Der Saldo wurde neu berechnet.", "ok")
    return redirect(url_for("main.entry_detail", eid=e.id))


@bp.get("/entries/<int:eid>/payments/preview")
@login_required
def payment_preview(eid):
    e = own_entry(eid)
    skip = _int_arg("exclude")
    before = svc.evaluate(e, skip_payment_id=skip)
    try:
        vals = parse_payment_form(request.args, e, skip_id=skip)
    except ValueError as exc:
        return jsonify(error=str(exc), before=util.fmt_eur(before.saldo))
    extra = eng.PaymentInput(vals["kind"], vals["paid_on"], vals["amount_cents"], vals["units"], vals["order_mode"])
    after = svc.evaluate(e, skip_payment_id=skip, extra=extra)
    return jsonify(before=util.fmt_eur(before.saldo), after=util.fmt_eur(after.saldo),
                   open_surcharge=util.fmt_eur(after.open_surcharge), open_principal=util.fmt_eur(after.open_principal),
                   overpaid=util.fmt_eur(after.overpaid) if after.overpaid else "")


# ---------------------------------------------------------------- Kontakte
def parse_contact_form(form):
    first = (form.get("first_name") or "").strip()[:80]
    if not first:
        raise ValueError("Der Vorname ist Pflicht.")
    rel = form.get("relation", "other")
    if rel not in RELATIONS:
        raise ValueError("Ungültige Beziehungsart.")
    return first, ((form.get("nickname") or "").strip()[:80] or None), rel


@bp.get("/contacts")
@login_required
def contacts():
    balances = svc.contact_balances(g.user.id)
    items = [(c, balances.get(c.id, 0)) for c in _contacts()]
    return render_template("contacts.html", items=items, RELATIONS=RELATIONS)


@bp.route("/contacts/new", methods=["GET", "POST"])
@login_required
def contact_new():
    if request.method == "POST":
        try:
            first, nick, rel = parse_contact_form(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("contact_form.html", f=request.form, RELATIONS=RELATIONS, edit=False), 400
        Session.add(Contact(user_id=g.user.id, first_name=first, nickname=nick, relation=rel))
        Session.commit()
        flash("Kontakt angelegt.", "ok")
        return redirect(url_for("main.contacts"))
    return render_template("contact_form.html", f={"relation": "friend"}, RELATIONS=RELATIONS, edit=False)


@bp.route("/contacts/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def contact_edit(cid):
    c = own_contact(cid)
    if request.method == "POST":
        try:
            c.first_name, c.nickname, c.relation = parse_contact_form(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("contact_form.html", f=request.form, RELATIONS=RELATIONS, edit=True, c=c), 400
        Session.commit()
        flash("Kontakt gespeichert.", "ok")
        return redirect(url_for("main.contacts"))
    return render_template("contact_form.html", f={"first_name": c.first_name, "nickname": c.nickname or "",
                                                   "relation": c.relation}, RELATIONS=RELATIONS, edit=True, c=c)


@bp.post("/contacts/<int:cid>/delete")
@login_required
def contact_delete(cid):
    c = own_contact(cid)
    if c.entries:
        flash("Der Kontakt hat noch Einträge. Lösche zuerst diese Einträge.", "error")
        return redirect(url_for("main.contacts"))
    Session.delete(c)
    Session.commit()
    flash("Kontakt gelöscht.", "ok")
    return redirect(url_for("main.contacts"))


# ---------------------------------------------------------------- Einstellungen
def _active_grant(user_id):
    return Session.scalar(select(SupportGrant).where(
        SupportGrant.user_id == user_id, SupportGrant.revoked_at.is_(None), SupportGrant.expires_at > utcnow())
        .order_by(SupportGrant.id.desc()))


def _settings_page(code=None):
    log = Session.scalars(select(SupportLog).where(SupportLog.user_id == g.user.id)
                          .order_by(SupportLog.id.desc()).limit(50)).all()
    return render_template("settings.html", grant=_active_grant(g.user.id), log=log, new_code=code,
                           EVENTS={"grant_created": "Freigabecode erzeugt", "grant_revoked": "Freigabecode widerrufen",
                                   "access_ok": "Support-Zugriff gestartet", "access_failed": "Fehlversuch mit Code",
                                   "access_ended": "Support-Zugriff beendet"})


@bp.get("/settings")
@login_required
def settings():
    return _settings_page()


@bp.post("/settings/password")
@login_required
def change_password():
    cur, new = request.form.get("current", ""), request.form.get("password", "")
    err = check_new_password(new, request.form.get("password2", ""))
    if not verify_password(g.user.password_hash, cur):
        err = "Das aktuelle Passwort ist falsch."
    if err:
        flash(err, "error")
        return redirect(url_for("main.settings"))
    g.user.password_hash = hash_password(new)
    g.user.must_change_password = False
    g.user.session_version += 1
    Session.commit()
    session["sv"] = g.user.session_version
    flash("Passwort geändert. Andere Sitzungen wurden abgemeldet.", "ok")
    return redirect(url_for("main.settings"))


@bp.post("/settings/support/create")
@login_required
def support_create():
    now = utcnow()
    for gr in Session.scalars(select(SupportGrant).where(SupportGrant.user_id == g.user.id, SupportGrant.revoked_at.is_(None))):
        gr.revoked_at = now
    code = f"{secrets.randbelow(10 ** 6):06d}"
    Session.add(SupportGrant(user_id=g.user.id, code_hash=support_code_hash(g.user.id, code),
                             expires_at=now + timedelta(hours=24)))
    Session.add(SupportLog(user_id=g.user.id, event="grant_created", ip=client_ip()))
    Session.commit()
    return _settings_page(code)


@bp.post("/settings/support/revoke")
@login_required
def support_revoke():
    gr = _active_grant(g.user.id)
    if gr:
        gr.revoked_at = utcnow()
        Session.add(SupportLog(user_id=g.user.id, event="grant_revoked", ip=client_ip()))
        Session.commit()
        flash("Freigabecode widerrufen. Laufende Support-Sitzungen sind beendet.", "ok")
    return redirect(url_for("main.settings"))
