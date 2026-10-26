"""Kombi-Engine: reine Rechenlogik ohne Framework- oder DB-Abhängigkeit.

Alle Beträge sind ganzzahlige Cent. Es werden keine Fließkommazahlen verwendet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

CAP_NONE, CAP_AMOUNT, CAP_WEEKS = "none", "amount", "weeks"
SURCHARGE_FIRST, PRINCIPAL_FIRST = "surcharge_first", "principal_first"
ST_OPEN, ST_PARTIAL, ST_DONE = "open", "partial", "done"


@dataclass(frozen=True)
class EntryInput:
    loan_date: date
    principal_cents: int
    quantity: Optional[int] = None  # nur Geräte/Objekte (Einheit "g")
    rate_cents: int = 0
    overrides: Dict[int, int] = field(default_factory=dict)  # Woche -> Cent
    cap_type: str = CAP_NONE
    cap_value: int = 0  # Cent (amount) oder Wochen (weeks)


@dataclass(frozen=True)
class PaymentInput:
    kind: str  # "money" | "return"
    paid_on: date
    amount_cents: int = 0
    units: int = 0
    order: str = SURCHARGE_FIRST


@dataclass(frozen=True)
class Result:
    days: int
    weeks: int  # nach Formel ceil(d/7), ungedeckelt
    weeks_counted: int  # nach Wochen-Deckel
    surcharge_total: int
    cap_reached: bool
    principal_total: int
    returned_units: int
    returned_value: int
    money_paid: int
    open_surcharge: int
    open_principal: int
    saldo: int
    overpaid: int
    status: str
    repaid_total: int  # Geldzahlungen + Wert der Rückgaben


def days_since(loan_date: date, today: date) -> int:
    """Tag 1 ist das Leihdatum selbst."""
    return (today - loan_date).days + 1


def weeks_for_days(d: int) -> int:
    return 0 if d <= 0 else -(-d // 7)  # ceil(d/7) ohne Float


def week_rate(entry: EntryInput, week_no: int) -> int:
    return entry.overrides.get(week_no, entry.rate_cents)


def counted_weeks(entry: EntryInput, weeks: int) -> int:
    if entry.cap_type == CAP_WEEKS:
        return min(weeks, max(0, entry.cap_value))
    return weeks


def surcharge_for_weeks(entry: EntryInput, weeks: int) -> int:
    """Gesamtaufpreis nach `weeks` Leihwochen inkl. Deckel."""
    wc = counted_weeks(entry, weeks)
    total = sum(week_rate(entry, i) for i in range(1, wc + 1))
    if entry.cap_type == CAP_AMOUNT:
        total = min(total, max(0, entry.cap_value))
    return total


def cap_reached(entry: EntryInput, weeks: int) -> bool:
    if entry.cap_type == CAP_WEEKS:
        return weeks >= entry.cap_value
    if entry.cap_type == CAP_AMOUNT:
        raw = sum(week_rate(entry, i) for i in range(1, weeks + 1))
        return raw >= entry.cap_value
    return False


def returned_value(entry: EntryInput, units: int) -> int:
    """Kumulativ gerechnet, damit die letzte Rückgabe exakt auf den Gesamtwert aufgeht."""
    if not entry.quantity:
        return 0
    u = max(0, min(units, entry.quantity))
    return entry.principal_cents * u // entry.quantity


def compute(entry: EntryInput, payments: Sequence[PaymentInput], today: date) -> Result:
    d = days_since(entry.loan_date, today)
    weeks = weeks_for_days(d)
    wc = counted_weeks(entry, weeks)
    surcharge = surcharge_for_weeks(entry, weeks)

    r_units = sum(p.units for p in payments if p.kind == "return")
    if entry.quantity:
        r_units = min(r_units, entry.quantity)
    else:
        r_units = 0
    r_value = returned_value(entry, r_units)
    principal_open = entry.principal_cents - r_value

    money = [p for p in payments if p.kind == "money"]
    money.sort(key=lambda p: p.paid_on)  # stabil: Reihenfolge der Eingabe bleibt bei Gleichstand
    so, po, paid = surcharge, principal_open, 0
    for p in money:
        amt = p.amount_cents
        paid += amt
        if p.order == PRINCIPAL_FIRST:
            a = min(amt, po); po -= a; amt -= a
            b = min(amt, so); so -= b
        else:
            a = min(amt, so); so -= a; amt -= a
            b = min(amt, po); po -= b

    saldo = so + po
    overpaid = max(0, paid - (surcharge + principal_open))
    repaid = paid + r_value
    if saldo <= 0:
        status = ST_DONE
    elif paid > 0 or r_units > 0:
        status = ST_PARTIAL
    else:
        status = ST_OPEN
    return Result(d, weeks, wc, surcharge, cap_reached(entry, weeks), entry.principal_cents,
                  r_units, r_value, paid, so, po, saldo, overpaid, status, repaid)


def schedule(entry: EntryInput, weeks: int) -> List[dict]:
    """Wochenplan inkl. kumuliertem (gedeckeltem) Aufpreis."""
    out, cum = [], 0
    for i in range(1, weeks + 1):
        counted = not (entry.cap_type == CAP_WEEKS and i > entry.cap_value)
        rate = week_rate(entry, i) if counted else 0
        cum += rate
        if entry.cap_type == CAP_AMOUNT:
            cum = min(cum, entry.cap_value)
        out.append({"week": i, "rate": rate, "cumulative": cum,
                    "override": i in entry.overrides, "counted": counted})
    return out
