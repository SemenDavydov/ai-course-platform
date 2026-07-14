"""add one-time login token (вход на сайт из Telegram-бота)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-14

Нужно для миграции покупателей из бота: у части из них в базе нет email,
поэтому письмо «установите пароль» им не отправить. Бот знает telegram_id,
выдаёт одноразовый токен, сайт меняет его на сессию.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("login_token", sa.String(), nullable=True))
    op.add_column("users", sa.Column("login_token_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_login_token", "users", ["login_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_login_token", table_name="users")
    op.drop_column("users", "login_token_sent_at")
    op.drop_column("users", "login_token")
