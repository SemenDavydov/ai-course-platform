"""make telegram_id nullable for admins

Revision ID: xxxx_make_telegram_id_nullable
Revises: ef98325e7e62
Create Date: 2026-03-14 09:50:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'xxxx_make_telegram_id_nullable'
down_revision = 'ef98325e7e62'  # ID предыдущей миграции
branch_labels = None
depends_on = None


def upgrade():
    # Делаем telegram_id опциональным (разрешаем NULL)
    op.alter_column('users', 'telegram_id',
                    existing_type=sa.BIGINT(),
                    nullable=True)


def downgrade():
    # Возвращаем обратно (запрещаем NULL)
    # ВАЖНО: перед откатом нужно убедиться, что нет NULL значений у админов
    op.execute("UPDATE users SET telegram_id = 0 WHERE telegram_id IS NULL AND role IN ('admin', 'superadmin')")
    op.alter_column('users', 'telegram_id',
                    existing_type=sa.BIGINT(),
                    nullable=False)