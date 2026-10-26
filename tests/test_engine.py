from datetime import date, timedelta

import pytest

from app import engine as E
from app.util import fmt_eur, parse_eur

TODAY = date(2026, 10, 26)


def loan_for_day(d):
    """Leihdatum so wählen, dass heute genau Tag d ist (Tag 1 = Leihdatum)."""
    return TODAY - timedelta(days=d - 1)


def mk(d, principal=10000, rate=500, cap_type="none", cap_value=0, overrides=None, quantity=None):
    return E.EntryInput(loan_for_day(d), principal, quantity, rate, overrides or {}, cap_type, cap_value)


def run(entry, pays=()):
    return E.compute(entry, list(pays), TODAY)


# --- Abnahmekriterium 1
def test_ac1():
    r = run(mk(1))
    assert (r.weeks, r.surcharge_total, r.saldo) == (1, 500, 10500)


# --- Abnahmekriterium 2
@pytest.mark.parametrize("d,weeks,saldo", [(7, 1, 10500), (8, 2, 11000), (15, 3, 11500)])
def test_ac2(d, weeks, saldo):
    r = run(mk(d))
    assert (r.weeks, r.saldo) == (weeks, saldo)


# --- 3: Wochen-Deckel
def test_ac3():
    r = run(mk(31, rate=1000, cap_type="weeks", cap_value=4))
    assert r.weeks == 5 and r.weeks_counted == 4
    assert (r.surcharge_total, r.saldo) == (4000, 14000)
    assert r.cap_reached


# --- 4: Betrags-Deckel
def test_ac4():
    r = run(mk(22, rate=2000, cap_type="amount", cap_value=5000))
    assert r.weeks == 4 and r.surcharge_total == 5000 and r.saldo == 15000


# --- 5: zinsfrei
def test_ac5():
    assert run(mk(100, rate=0)).saldo == 10000


# --- 6: Verrechnungsreihenfolge
def test_ac6_surcharge_first():
    pay = E.PaymentInput("money", TODAY, 2000, order=E.SURCHARGE_FIRST)
    r = run(mk(15), [pay])
    assert (r.open_surcharge, r.open_principal, r.saldo) == (0, 9500, 9500)
    assert r.status == E.ST_PARTIAL


def test_ac6_principal_first():
    pay = E.PaymentInput("money", TODAY, 2000, order=E.PRINCIPAL_FIRST)
    r = run(mk(15), [pay])
    assert (r.open_principal, r.open_surcharge, r.saldo) == (8000, 1500, 9500)


# --- 7: Geräte
def test_ac7_items():
    e = mk(30, principal=2000, rate=0, quantity=2)
    r1 = run(e, [E.PaymentInput("return", TODAY, units=1)])
    assert (r1.open_principal, r1.saldo, r1.status) == (1000, 1000, E.ST_PARTIAL)
    r2 = run(e, [E.PaymentInput("return", TODAY, units=1), E.PaymentInput("return", TODAY, units=1)])
    assert (r2.saldo, r2.status) == (0, E.ST_DONE)


# --- 8: Überschreibung einer Woche
def test_ac8_override():
    r = run(mk(15, overrides={2: 800}))
    assert r.surcharge_total == 500 + 800 + 500 and r.saldo == 10000 + 1800


# --- Weitere Randfälle
def test_week_boundaries():
    assert [E.weeks_for_days(d) for d in (0, 1, 7, 8, 14, 15)] == [0, 1, 1, 2, 2, 3]


def test_overpayment_never_negative():
    r = run(mk(1), [E.PaymentInput("money", TODAY, 20000)])
    assert r.saldo == 0 and r.overpaid == 9500 and r.status == E.ST_DONE


def test_status_open():
    assert run(mk(3)).status == E.ST_OPEN


def test_uneven_item_value_last_unit_exact():
    e = mk(1, principal=1000, rate=0, quantity=3)
    pays = [E.PaymentInput("return", TODAY, units=1) for _ in range(3)]
    assert run(e, pays[:1]).open_principal == 1000 - 333
    assert run(e, pays).saldo == 0


def test_surcharge_independent_of_partial_payments():
    a = run(mk(15)).surcharge_total
    b = run(mk(15), [E.PaymentInput("money", TODAY, 5000)]).surcharge_total
    assert a == b == 1500


def test_cap_with_override_amount():
    r = run(mk(22, rate=500, overrides={1: 5000}, cap_type="amount", cap_value=5500))
    assert r.surcharge_total == 5500


def test_future_loan_has_no_surcharge():
    r = E.compute(E.EntryInput(TODAY + timedelta(days=3), 1000, None, 500), [], TODAY)
    assert r.weeks == 0 and r.saldo == 1000


def test_money_format_and_parse():
    assert fmt_eur(123456) == "1.234,56 €" and fmt_eur(5) == "0,05 €"
    assert parse_eur("1.234,56 €") == 123456 and parse_eur("5") == 500 and parse_eur("5.5") == 550
    assert parse_eur("1.234") == 123400
    for bad in ("", "abc", "1,234", "-5"):
        with pytest.raises(ValueError):
            parse_eur(bad)
