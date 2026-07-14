"""add_web_user_fields

Revision ID: a2b3c4d5e6f7
Revises: add_accepted_offer
Create Date: 2026-04-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'add_accepted_offer'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Новые колонки в таблице users ---
    op.add_column('users', sa.Column(
        'email_verified', sa.Boolean(), nullable=False, server_default='false'
    ))
    op.add_column('users', sa.Column(
        'email_verification_token', sa.String(), nullable=True
    ))
    op.add_column('users', sa.Column(
        'email_verification_sent_at', sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column('users', sa.Column(
        'name', sa.String(), nullable=True
    ))
    op.add_column('users', sa.Column(
        'avatar_url', sa.String(), nullable=True
    ))
    op.add_column('users', sa.Column(
        'registration_source', sa.String(), nullable=False, server_default='bot_migrated'
    ))

    # Для уже существующих записей (бот-пользователи):
    # email_verified = True (они подтверждены фактом покупки через бот)
    # registration_source = 'bot_migrated' (уже стоит по умолчанию)
    op.execute(
        "UPDATE users SET email_verified = TRUE WHERE telegram_id IS NOT NULL"
    )

    # --- Новая таблица user_sessions ---
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('session_token', sa.String(), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.create_index('ix_user_sessions_session_token', 'user_sessions', ['session_token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_user_sessions_session_token', table_name='user_sessions')
    op.drop_table('user_sessions')

    op.drop_column('users', 'registration_source')
    op.drop_column('users', 'avatar_url')
    op.drop_column('users', 'name')
    op.drop_column('users', 'email_verification_sent_at')
    op.drop_column('users', 'email_verification_token')
    op.drop_column('users', 'email_verified')
