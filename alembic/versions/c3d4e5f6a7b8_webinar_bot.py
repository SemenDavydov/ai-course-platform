"""webinar bot tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webinar_subscribers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "lead_magnet_sent", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_webinar_subscribers_telegram_id",
        "webinar_subscribers",
        ["telegram_id"],
        unique=True,
    )

    op.create_table(
        "webinar_broadcast_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_key", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recipients_total", sa.Integer(), server_default="0"),
        sa.Column("recipients_ok", sa.Integer(), server_default="0"),
        sa.Column("recipients_fail", sa.Integer(), server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("campaign_key", name="uq_webinar_broadcast_campaign"),
    )


def downgrade() -> None:
    op.drop_table("webinar_broadcast_log")
    op.drop_index("ix_webinar_subscribers_telegram_id", table_name="webinar_subscribers")
    op.drop_table("webinar_subscribers")
