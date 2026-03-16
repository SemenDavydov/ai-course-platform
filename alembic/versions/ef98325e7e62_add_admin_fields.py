"""add_admin_fields

Revision ID: ef98325e7e62
Revises: 62381685edd7
Create Date: 2026-03-14 09:19:23.014893

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef98325e7e62'
down_revision: Union[str, Sequence[str], None] = '62381685edd7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # 1. Сначала обновим существующие NULL значения в video_id
    #    (ставим пустую строку вместо NULL)
    op.execute("UPDATE lessons SET video_id = '' WHERE video_id IS NULL")

    # 2. Теперь можно безопасно изменить колонку
    #    Делаем колонку NOT NULL с дефолтным значением пустой строки
    op.alter_column('lessons', 'video_id',
                    existing_type=sa.VARCHAR(),
                    nullable=False,
                    server_default='')

    # 3. Добавляем новые колонки в users
    op.add_column('users', sa.Column('password_hash', sa.VARCHAR(), nullable=True))
    op.add_column('users', sa.Column('role', sa.VARCHAR(), server_default='user', nullable=False))
    op.add_column('users', sa.Column('is_blocked', sa.Boolean(), server_default='false', nullable=False))

    # 4. Создаём таблицу для сессий админов
    op.create_table('admin_sessions',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('user_id', sa.Integer(), nullable=False),
                    sa.Column('session_token', sa.VARCHAR(), nullable=False),
                    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
                    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('session_token')
                    )
    op.create_index(op.f('ix_admin_sessions_session_token'), 'admin_sessions', ['session_token'], unique=True)
    # ### end Alembic commands ###


def downgrade():
    # Откатываем изменения в обратном порядке
    op.drop_index(op.f('ix_admin_sessions_session_token'), table_name='admin_sessions')
    op.drop_table('admin_sessions')
    op.drop_column('users', 'is_blocked')
    op.drop_column('users', 'role')
    op.drop_column('users', 'password_hash')

    # Возвращаем video_id в исходное состояние (разрешаем NULL)
    op.alter_column('lessons', 'video_id',
                    existing_type=sa.VARCHAR(),
                    nullable=True,
                    server_default=None)
    # ### end Alembic commands ###
