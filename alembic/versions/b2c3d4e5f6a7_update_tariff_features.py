"""update tariff features for Pro and VIP

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-01

"""
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

PRO_FEATURES = """- проверка ДЗ куратором в общем чате
- обратная связь и поддержка от куратора
- закрытый чат с куратором
- Доступ к материалам бессрочно
- поддержка куратора 6 месяцев
- Доступ к материалам на 3 месяца после окончания обучения
- Поддержка куратора в общем чате на 1 месяц после окончания обучения"""

VIP_FEATURES = """- личный чат с Лизой
- 3 личных zoom-разбора вашего сериала с Лизой
- Обратная связь в течение 1 месяца после обучения в личном чате
- Личная стратегия продвижения
- Дополнительный зум-созвон по пройденному материалу
- Доступ к материалам бессрочно"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE tariffs
        SET features_markdown = $${PRO_FEATURES}$$
        WHERE slug = 'pro'
          AND course_id IN (SELECT id FROM courses WHERE slug = 'ai-story')
        """
    )
    op.execute(
        f"""
        UPDATE tariffs
        SET features_markdown = $${VIP_FEATURES}$$
        WHERE slug = 'vip'
          AND course_id IN (SELECT id FROM courses WHERE slug = 'ai-story')
        """
    )


def downgrade() -> None:
    old_pro = """- проверка ДЗ куратором в общем чате
- обратная связь и поддержка от куратора
- закрытый чат с куратором
- Доступ к материалам бессрочно
- поддержка куратора 6 месяцев"""
    old_vip = """- личный чат с Лизой
- 3 личных zoom-разбора вашего сериала с Лизой
- Обратная связь в течение 1 месяца после обучения в личном чате
- Личная стратегия продвижения
- Дополнительный зум-созвон по пройденному материалу
- Доступ к материалам бессрочно
- Возможность получить коммерческий проект (при наличии)"""
    op.execute(
        f"""
        UPDATE tariffs
        SET features_markdown = $${old_pro}$$
        WHERE slug = 'pro'
          AND course_id IN (SELECT id FROM courses WHERE slug = 'ai-story')
        """
    )
    op.execute(
        f"""
        UPDATE tariffs
        SET features_markdown = $${old_vip}$$
        WHERE slug = 'vip'
          AND course_id IN (SELECT id FROM courses WHERE slug = 'ai-story')
        """
    )
