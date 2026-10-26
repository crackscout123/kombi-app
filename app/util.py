import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
MAX_CENTS = 10 ** 11


def env(name, default=None):
    """Liest NAME_FILE (Docker Secret) vor NAME."""
    path = os.environ.get(name + "_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            pass
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def env_bool(name, default=False):
    v = env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on", "ja")


def today_berlin():
    return datetime.now(TZ).date()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fmt_eur(cents):
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    c = abs(cents)
    return f"{sign}{c // 100:,}".replace(",", ".") + f",{c % 100:02d} €"


def fmt_date(d):
    return d.strftime("%d.%m.%Y") if d else ""


def fmt_dt(dt):
    if not dt:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def parse_eur(text):
    """'1.234,56 €' -> 123456. Wirft ValueError mit deutscher Meldung."""
    s = str(text or "").replace("€", "").replace(" ", "").replace("\u00a0", "")
    if not s:
        raise ValueError("Bitte einen Betrag angeben.")
    if not re.fullmatch(r"[0-9.,]+", s):
        raise ValueError("Ungültiger Betrag (erlaubt: 1.234,56).")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif not re.fullmatch(r"\d+\.\d{1,2}", s):
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("Ungültiger Betrag (erlaubt: 1.234,56).")
    if d != d.quantize(Decimal("0.01")):
        raise ValueError("Höchstens zwei Nachkommastellen erlaubt.")
    cents = int(d * 100)
    if cents < 0 or cents > MAX_CENTS:
        raise ValueError("Betrag außerhalb des erlaubten Bereichs.")
    return cents


def parse_date(text):
    s = str(text or "").strip()
    for f in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise ValueError("Ungültiges Datum (Format TT.MM.JJJJ).")


def parse_int(text, lo=0, hi=10 ** 6, label="Zahl"):
    try:
        v = int(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label}: bitte eine ganze Zahl angeben.")
    if v < lo or v > hi:
        raise ValueError(f"{label}: erlaubt sind {lo} bis {hi}.")
    return v
