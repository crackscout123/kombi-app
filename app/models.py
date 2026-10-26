from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .util import utcnow

BIG = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(10), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def status_label(self):
        if not self.active:
            return "Gesperrt"
        return "Eingeladen" if not self.password_hash else "Aktiv"


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    first_name: Mapped[str] = mapped_column(String(80))
    nickname: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    relation: Mapped[str] = mapped_column(String(20), default="other")  # friend|family|work|other
    entries: Mapped[List["Entry"]] = relationship(back_populates="contact")

    @property
    def display(self):
        return f"{self.first_name} („{self.nickname}“)" if self.nickname else self.first_name


class Entry(Base):
    __tablename__ = "entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # give (Forderung) | take (Verbindlichkeit)
    category: Mapped[str] = mapped_column(String(8))  # money | item
    title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    loan_date: Mapped[date] = mapped_column(Date)
    principal_cents: Mapped[int] = mapped_column(BIG)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rate_cents: Mapped[int] = mapped_column(BIG, default=0)
    cap_type: Mapped[str] = mapped_column(String(8), default="none")
    cap_value: Mapped[int] = mapped_column(BIG, default=0)
    exclude_from_total: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    contact: Mapped[Contact] = relationship(back_populates="entries")
    overrides: Mapped[List["WeekOverride"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    payments: Mapped[List["Payment"]] = relationship(cascade="all, delete-orphan", lazy="selectin",
                                                     order_by="Payment.paid_on, Payment.id")

    @property
    def label(self):
        if self.category == "item":
            return f"{self.quantity}g {self.title}"
        return self.title or "Geld"


class WeekOverride(Base):
    __tablename__ = "week_overrides"
    __table_args__ = (UniqueConstraint("entry_id", "week_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    rate_cents: Mapped[int] = mapped_column(BIG)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # money | return
    amount_cents: Mapped[int] = mapped_column(BIG, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    order_mode: Mapped[str] = mapped_column(String(20), default="surcharge_first")
    paid_on: Mapped[date] = mapped_column(Date)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Token(Base):
    __tablename__ = "tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SupportGrant(Base):
    __tablename__ = "support_grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    used_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SupportLog(Base):
    __tablename__ = "support_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    admin_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    admin_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event: Mapped[str] = mapped_column(String(20))
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(190))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_rate_key_time", "key", "created_at"),)
