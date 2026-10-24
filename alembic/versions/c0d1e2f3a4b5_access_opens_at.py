"""user_course_access.opens_at — уроки открываются по окончании периода продаж

NULL = уроки доступны сразу (все существующие доступы, выдача из админки).

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_course_access",
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_course_access", "opens_at")
