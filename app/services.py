from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import engine as eng
from .db import Session
from .models import Entry
from .util import today_berlin


def to_inputs(entry, skip_payment_id=None, extra=None):
    ei = eng.EntryInput(
        loan_date=entry.loan_date, principal_cents=entry.principal_cents,
        quantity=entry.quantity if entry.category == "item" else None,
        rate_cents=entry.rate_cents, overrides={o.week_no: o.rate_cents for o in entry.overrides},
        cap_type=entry.cap_type, cap_value=entry.cap_value)
    pays = [eng.PaymentInput(p.kind, p.paid_on, p.amount_cents, p.units, p.order_mode)
            for p in entry.payments if p.id != skip_payment_id]
    if extra is not None:
        pays.append(extra)
    return ei, pays


def evaluate(entry, today=None, skip_payment_id=None, extra=None):
    ei, pays = to_inputs(entry, skip_payment_id, extra)
    return eng.compute(ei, pays, today or today_berlin())


def entries_for(user_id):
    return Session.scalars(
        select(Entry).where(Entry.user_id == user_id)
        .options(selectinload(Entry.contact))
        .order_by(Entry.loan_date.desc(), Entry.id.desc())).all()


def dashboard_data(user_id, direction=None, status=None, contact_id=None):
    today = today_berlin()
    rows = [(e, evaluate(e, today)) for e in entries_for(user_id)]
    claims = sum(r.saldo for e, r in rows if e.direction == "give")
    debts = sum(r.saldo for e, r in rows if e.direction == "take")
    shown = [(e, r) for e, r in rows
             if (not direction or e.direction == direction)
             and (not status or r.status == status)
             and (not contact_id or e.contact_id == contact_id)]
    return {"rows": shown, "claims": claims, "debts": debts, "net": claims - debts}


def contact_balances(user_id):
    out = {}
    for e, r in ((e, evaluate(e)) for e in entries_for(user_id)):
        if e.exclude_from_total:
            continue
        out[e.contact_id] = out.get(e.contact_id, 0) + (r.saldo if e.direction == "give" else -r.saldo)
    return out


def system_stats():
    """Nur aggregierte Zahlen – keine Namen, keine Buchungsdetails."""
    from .models import User
    from sqlalchemy import func
    lent = surcharges = repaid = open_vol = 0
    for e in Session.scalars(select(Entry)):
        r = evaluate(e)
        lent += e.principal_cents
        surcharges += r.surcharge_total
        repaid += r.repaid_total
        open_vol += r.saldo
    active = Session.scalar(select(func.count()).select_from(User)
                            .where(User.active.is_(True), User.password_hash.is_not(None)))
    return {"lent": lent, "surcharges": surcharges, "repaid": repaid, "open": open_vol, "active_users": active}
