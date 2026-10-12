"""Модели SQLAlchemy. Схема — docs/ARCHITECTURE.md, раздел «Данные».

- `id` — bigint автоинкремент (в SQLite — INTEGER, иначе нет автоинкремента);
- время — `timestamptz` в UTC; из базы всегда возвращается aware datetime в UTC
  (SQLite хранит без пояса — `UTCDateTime` дописывает UTC при чтении);
- JSON — `sa.JSON` (работает и в Postgres, и в SQLite).

Любое изменение здесь — новая миграция в `alembic/versions/`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """`timestamptz`: пишет в UTC, читает aware datetime в UTC (в том числе из SQLite)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


# Значения строковых полей-перечислений (без CHECK в базе, чтобы P1 добавлял статусы без миграции).
ROLES = ("consultant", "opex", "transformation", "other")
EVENT_CODES = ("practicum", "course", "demo_day", "course_1", "course_2", "course_3", "course_4", "waitlist")
CHANNELS = ("bot", "site")
REGISTRATION_STATUSES = ("registered", "applied", "confirmed", "rejected", "paid", "cancelled")
DELIVERY_STATUSES = ("sent", "failed", "blocked")


class Person(TimestampMixin, Base):
    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("telegram_id"),
        Index("ix_people_telegram_username", "telegram_username"),
        Index("ix_people_email", "email"),
        Index("ix_people_phone", "phone"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64))  # без @, в нижнем регистре
    tg_first_name: Mapped[str | None] = mapped_column(String(256))
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))  # в нижнем регистре
    phone: Mapped[str | None] = mapped_column(String(32))  # E.164
    role: Mapped[str | None] = mapped_column(String(32))  # ROLES
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source: Mapped[str | None] = mapped_column(String(255))  # метка первого входа, не перезаписывается
    utm: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    registrations: Mapped[list[Registration]] = relationship(back_populates="person")

    def __repr__(self) -> str:  # без ПД
        return f"<Person id={self.id}>"


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)  # EVENT_CODES
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    join_url: Mapped[str | None] = mapped_column(String(1000))  # задаёт владелец, sync не трогает
    recording_url: Mapped[str | None] = mapped_column(String(1000))  # задаёт владелец, sync не трогает

    def __repr__(self) -> str:
        return f"<Event {self.code}>"


class Registration(TimestampMixin, Base):
    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("person_id", "event_id"),
        UniqueConstraint("site_id"),
        Index("ix_registrations_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # CHANNELS
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="registered", server_default="registered")
    tariff: Mapped[str | None] = mapped_column(String(32))
    task: Mapped[str | None] = mapped_column(Text)
    invoice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    source: Mapped[str | None] = mapped_column(String(255))
    utm: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    site_id: Mapped[str | None] = mapped_column(String(64))  # id заявки с сайта

    person: Mapped[Person] = relationship(back_populates="registrations")
    event: Mapped[Event] = relationship()

    def __repr__(self) -> str:
        return f"<Registration id={self.id} person={self.person_id} event={self.event_id}>"


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("person_id", "event_id", "kind"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # invite_24h, reminder_1h, ...
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # DELIVERY_STATUSES
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (Index("ix_questions_person_id", "person_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bot_answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, server_default=func.now(), nullable=False
    )
    admin_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    answer_text: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FsmState(Base):
    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)  # строка из StorageKey
    state: Mapped[str | None] = mapped_column(String(255))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )
