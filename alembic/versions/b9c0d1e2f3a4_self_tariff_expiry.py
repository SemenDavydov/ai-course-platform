"""self tariff (Самостоятельный) + access expiry

- user_course_access.expires_at: NULL = бессрочно (все существующие доступы)
- user_course_access.expiry_reminder_sent_at: письмо «доступ заканчивается» отправлено
- тариф self для ai-story: 8990 ₽, 2 месяца, без рассрочки

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SELF_FEATURES = """- Доступ ко всем материалам на 2 месяца
- Полностью самостоятельное обучение на базе уроков курса
- Общий чат с другими учениками"""


def upgrade() -> None:
    op.add_column(
        "user_course_access",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_course_access",
        sa.Column("expiry_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_user_course_access_expires_at", "user_course_access", ["expires_at"]
    )

    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO tariffs
                (course_id, slug, name, price, old_price, is_active, features_markdown, sort_order)
            SELECT c.id, 'self', :name, :price, NULL, true, :features, -1
            FROM courses c
            WHERE c.slug = 'ai-story'
              AND NOT EXISTS (
                  SELECT 1 FROM tariffs t WHERE t.course_id = c.id AND t.slug = 'self'
              )
            """
        ),
        {"name": "Самостоятельный", "price": 8990.0, "features": SELF_FEATURES},
    )


def downgrade() -> None:
    # Тариф, на который уже ссылаются платежи, не удаляем — только выключаем,
    # чтобы история оплат не потеряла tariff_id.
    op.execute(
        """
        UPDATE tariffs SET is_active = false
        WHERE slug = 'self'
          AND EXISTS (SELECT 1 FROM payments p WHERE p.tariff_id = tariffs.id)
        """
    )
    op.execute(
        """
        DELETE FROM tariffs
        WHERE slug = 'self'
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.tariff_id = tariffs.id)
        """
    )
    op.drop_index("ix_user_course_access_expires_at", table_name="user_course_access")
    op.drop_column("user_course_access", "expiry_reminder_sent_at")
    op.drop_column("user_course_access", "expires_at")
