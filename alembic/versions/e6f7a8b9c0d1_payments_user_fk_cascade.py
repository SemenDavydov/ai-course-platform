"""payments.user_id ON DELETE CASCADE

Revision ID: e6f7a8b9c0d1
Revises: c4d5e6f7a8b9
Create Date: 2026-04-27

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("payments_user_id_fkey", "payments", type_="foreignkey")
    op.create_foreign_key(
        "payments_user_id_fkey",
        "payments",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("payments_user_id_fkey", "payments", type_="foreignkey")
    op.create_foreign_key(
        "payments_user_id_fkey",
        "payments",
        "users",
        ["user_id"],
        ["id"],
    )
