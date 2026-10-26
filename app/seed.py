from datetime import timedelta

from sqlalchemy import select

from .db import Session
from .models import Contact, Entry, Payment, User, WeekOverride
from .security import hash_password
from .util import today_berlin


def seed_demo(password):
    if Session.scalar(select(User).where(User.username == "demo")):
        return
    u = User(username="demo", email="demo@example.invalid", password_hash=hash_password(password))
    Session.add(u)
    Session.flush()
    t = today_berlin()
    anna = Contact(user_id=u.id, first_name="Anna", nickname="Annie", relation="friend")
    ben = Contact(user_id=u.id, first_name="Ben", relation="family")
    chris = Contact(user_id=u.id, first_name="Chris", relation="work")
    Session.add_all([anna, ben, chris])
    Session.flush()
    e1 = Entry(user_id=u.id, contact_id=anna.id, direction="give", category="money", title="Urlaubskasse",
               loan_date=t - timedelta(days=14), principal_cents=10000, rate_cents=500)
    e2 = Entry(user_id=u.id, contact_id=ben.id, direction="take", category="money", loan_date=t - timedelta(days=30),
               principal_cents=10000, rate_cents=1000, cap_type="weeks", cap_value=4)
    e3 = Entry(user_id=u.id, contact_id=chris.id, direction="give", category="item", title="Ladekabel", quantity=2,
               loan_date=t - timedelta(days=5), principal_cents=2000, rate_cents=0)
    e4 = Entry(user_id=u.id, contact_id=anna.id, direction="give", category="money", loan_date=t - timedelta(days=21),
               principal_cents=10000, rate_cents=2000, cap_type="amount", cap_value=5000, exclude_from_total=True)
    Session.add_all([e1, e2, e3, e4])
    Session.flush()
    Session.add(WeekOverride(entry_id=e1.id, week_no=2, rate_cents=800))
    Session.add(Payment(entry_id=e1.id, kind="money", amount_cents=2000, order_mode="surcharge_first",
                        paid_on=t - timedelta(days=3), note="Teilzahlung"))
    Session.add(Payment(entry_id=e3.id, kind="return", units=1, paid_on=t - timedelta(days=1), note="1 Kabel zurück"))
    Session.commit()
