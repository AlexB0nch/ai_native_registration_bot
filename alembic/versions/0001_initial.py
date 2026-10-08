"""initial: people, events, registrations, deliveries, questions, fsm_states

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BigIntPK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _ts(name: str, nullable: bool = False, server_default: bool = True) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if server_default else None,
    )


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("tg_first_name", sa.String(length=256), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        _ts("consent_at", nullable=True, server_default=False),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("utm", sa.JSON(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_people"),
        sa.UniqueConstraint("telegram_id", name="uq_people_telegram_id"),
    )
    op.create_index("ix_people_telegram_username", "people", ["telegram_username"])
    op.create_index("ix_people_email", "people", ["email"])
    op.create_index("ix_people_phone", "people", ["phone"])

    op.create_table(
        "events",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        _ts("starts_at", nullable=True, server_default=False),
        _ts("ends_at", nullable=True, server_default=False),
        sa.Column("join_url", sa.String(length=1000), nullable=True),
        sa.Column("recording_url", sa.String(length=1000), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.UniqueConstraint("code", name="uq_events_code"),
    )

    op.create_table(
        "registrations",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("person_id", BigIntPK, nullable=False),
        sa.Column("event_id", BigIntPK, nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="registered", nullable=False),
        sa.Column("tariff", sa.String(length=32), nullable=True),
        sa.Column("task", sa.Text(), nullable=True),
        sa.Column("invoice", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("utm", sa.JSON(), nullable=True),
        sa.Column("site_id", sa.String(length=64), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name="fk_registrations_person_id_people", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_registrations_event_id_events", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_registrations"),
        sa.UniqueConstraint("person_id", "event_id", name="uq_registrations_person_id_event_id"),
        sa.UniqueConstraint("site_id", name="uq_registrations_site_id"),
    )
    op.create_index("ix_registrations_event_id", "registrations", ["event_id"])

    op.create_table(
        "deliveries",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("person_id", BigIntPK, nullable=False),
        sa.Column("event_id", BigIntPK, nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        _ts("sent_at", nullable=True, server_default=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name="fk_deliveries_person_id_people", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_deliveries_event_id_events", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_deliveries"),
        sa.UniqueConstraint("person_id", "event_id", "kind", name="uq_deliveries_person_id_event_id_kind"),
    )

    op.create_table(
        "questions",
        sa.Column("id", BigIntPK, autoincrement=True, nullable=False),
        sa.Column("person_id", BigIntPK, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bot_answer", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.Column("admin_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("admin_message_id", sa.BigInteger(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        _ts("answered_at", nullable=True, server_default=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name="fk_questions_person_id_people", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_questions"),
    )
    op.create_index("ix_questions_person_id", "questions", ["person_id"])

    op.create_table(
        "fsm_states",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("key", name="pk_fsm_states"),
    )


def downgrade() -> None:
    op.drop_table("fsm_states")
    op.drop_index("ix_questions_person_id", table_name="questions")
    op.drop_table("questions")
    op.drop_table("deliveries")
    op.drop_index("ix_registrations_event_id", table_name="registrations")
    op.drop_table("registrations")
    op.drop_table("events")
    op.drop_index("ix_people_phone", table_name="people")
    op.drop_index("ix_people_email", table_name="people")
    op.drop_index("ix_people_telegram_username", table_name="people")
    op.drop_table("people")
