"""waitlist_applications + site_settings for enrollment/sales mode

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )
    op.create_table(
        "waitlist_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("social", sa.String(length=255), nullable=True),
        sa.Column("accepted_privacy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_waitlist_applications_email",
        "waitlist_applications",
        ["email"],
    )
    op.execute(
        "INSERT INTO site_settings (key, value) VALUES ('landing_mode', 'enrollment')"
    )


def downgrade() -> None:
    op.drop_index("ix_waitlist_applications_email", table_name="waitlist_applications")
    op.drop_table("waitlist_applications")
    op.drop_table("site_settings")
