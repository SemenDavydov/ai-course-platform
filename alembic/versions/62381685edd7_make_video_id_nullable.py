"""make_video_id_nullable

Revision ID: 62381685edd7
Revises: 443a7d144ca9
Create Date: 2026-03-13 19:08:00.204189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62381685edd7'
down_revision: Union[str, Sequence[str], None] = '443a7d144ca9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Делаем поле video_id опциональным (разрешаем NULL)
    op.alter_column('lessons', 'video_id',
                    existing_type=sa.VARCHAR(),
                    nullable=True)


def downgrade():
    # Возвращаем обратно (запрещаем NULL)
    # ВАЖНО: перед откатом нужно убедиться, что нет NULL значений
    op.alter_column('lessons', 'video_id',
                    existing_type=sa.VARCHAR(),
                    nullable=False)
