"""AI STORY multicourse: modules, tariffs, entitlements

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-08-27

Adds Module/Tariff/UserCourseAccess, course slug/sort/legacy flags,
payment course/tariff FKs, seeds AI STORY course + Pro/VIP tariffs,
migrates has_access users to legacy entitlement.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AI_STORY_MODULES = [
    (
        1,
        "Введение в профессию",
        [
            "Как устроен мир ИИ-контента в 2026?",
            "Обзор инструментов",
            "Как оплатить зарубежные подписки",
        ],
    ),
    (
        2,
        "Идеи и сценарии вирусных ИИ-сериалов",
        [
            "Как рождаются идеи?",
            "Формула вирусной серии",
            "Как писать сценарий к сериалу",
            "Создание полноценного сценария для сериала",
        ],
    ),
    (
        3,
        "Создание собственного героя",
        [
            "От идеи до персонажа",
            "Создание персонажа",
            "Консистентность героя",
            "Мир сериала",
        ],
    ),
    (
        4,
        "Работа с генерацией",
        [
            "Основы генерации",
            "Создание раскадровок",
            "Генерация видео",
            "Практика",
        ],
    ),
    (
        5,
        "Монтаж и озвучка",
        [
            "Голос персонажа",
            "Музыка и звуки",
            "Монтаж видео",
        ],
    ),
    (
        6,
        "ИИ контент для коммерции",
        [
            "Что можно продавать бизнесу",
            "Как создать рекламу",
            "Коммерческая съёмка",
            "Монтаж коммерческого видео",
            "ИИ модели",
            "ИИ сериал для бизнеса",
        ],
    ),
    (
        7,
        "Монетизация и продвижение",
        [
            "Доход в ИИ бизнесе",
            "Портфолио ИИ креатора",
            "Продвижение",
            "Общение с клиентом",
            "База документов",
        ],
    ),
]

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
    op.add_column("courses", sa.Column("slug", sa.String(), nullable=True))
    op.add_column(
        "courses",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "courses",
        sa.Column("is_legacy", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index("ix_courses_slug", "courses", ["slug"], unique=True)

    op.create_table(
        "modules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("button_label", sa.String(), nullable=True),
    )

    op.add_column(
        "lessons",
        sa.Column("module_id", sa.Integer(), sa.ForeignKey("modules.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("old_price", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("features_markdown", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "user_course_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tariff_slug", sa.String(), nullable=False, server_default="legacy"),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_user_course_access_user_id", "user_course_access", ["user_id"])
    op.create_index("ix_user_course_access_course_id", "user_course_access", ["course_id"])
    op.create_unique_constraint(
        "uq_user_course_access_user_course", "user_course_access", ["user_id", "course_id"]
    )

    op.add_column(
        "payments",
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("courses.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("tariff_id", sa.Integer(), sa.ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("payments", sa.Column("tariff_slug", sa.String(), nullable=True))

    conn = op.get_bind()

    # Mark existing courses as legacy
    courses = conn.execute(sa.text("SELECT id FROM courses ORDER BY id")).fetchall()
    for i, row in enumerate(courses):
        slug = "ai-animations" if i == 0 else f"legacy-course-{row[0]}"
        conn.execute(
            sa.text(
                "UPDATE courses SET slug = :slug, sort_order = 100, is_legacy = true WHERE id = :id"
            ),
            {"slug": slug, "id": row[0]},
        )

    legacy_id = courses[0][0] if courses else None

    # Seed AI STORY
    result = conn.execute(
        sa.text(
            """
            INSERT INTO courses (title, description, price, is_published, slug, sort_order, is_legacy)
            VALUES (
                :title, :description, :price, true, 'ai-story', 0, false
            )
            RETURNING id
            """
        ),
        {
            "title": "AI STORY: воплоти свою историю",
            "description": (
                "Глобальная переработка курса: от идеи вирусного ИИ-сериала "
                "до монетизации и первых клиентов."
            ),
            "price": 9990.0,
        },
    )
    story_id = result.fetchone()[0]

    for mod_order, mod_title, lesson_titles in AI_STORY_MODULES:
        mod_result = conn.execute(
            sa.text(
                """
                INSERT INTO modules (course_id, title, "order", button_label)
                VALUES (:course_id, :title, :ord, :btn)
                RETURNING id
                """
            ),
            {
                "course_id": story_id,
                "title": mod_title,
                "ord": mod_order,
                "btn": f"Модуль {mod_order}",
            },
        )
        module_id = mod_result.fetchone()[0]
        for li, lesson_title in enumerate(lesson_titles, start=1):
            global_order = mod_order * 100 + li
            conn.execute(
                sa.text(
                    """
                    INSERT INTO lessons (course_id, module_id, title, description, video_id, "order")
                    VALUES (:course_id, :module_id, :title, :description, 'pending', :ord)
                    """
                ),
                {
                    "course_id": story_id,
                    "module_id": module_id,
                    "title": lesson_title,
                    "description": f"Модуль {mod_order}. Урок {li}: {lesson_title}",
                    "ord": global_order,
                },
            )

    for slug, name, price, old_price, sort_order, features in [
        ("pro", "Pro", 9990.0, 12990.0, 0, PRO_FEATURES),
        ("vip", "VIP", 29990.0, 34990.0, 1, VIP_FEATURES),
    ]:
        conn.execute(
            sa.text(
                """
                INSERT INTO tariffs
                    (course_id, slug, name, price, old_price, is_active, features_markdown, sort_order)
                VALUES
                    (:course_id, :slug, :name, :price, :old_price, true, :features, :sort_order)
                """
            ),
            {
                "course_id": story_id,
                "slug": slug,
                "name": name,
                "price": price,
                "old_price": old_price,
                "features": features,
                "sort_order": sort_order,
            },
        )

    # Migrate existing accessors to legacy course entitlement
    if legacy_id is not None:
        conn.execute(
            sa.text(
                """
                INSERT INTO user_course_access (user_id, course_id, tariff_slug, granted_at)
                SELECT id, :legacy_id, 'legacy', COALESCE(access_granted_at, now())
                FROM users
                WHERE has_access = true
                ON CONFLICT (user_id, course_id) DO NOTHING
                """
            ),
            {"legacy_id": legacy_id},
        )


def downgrade() -> None:
    op.drop_column("payments", "tariff_slug")
    op.drop_column("payments", "tariff_id")
    op.drop_column("payments", "course_id")
    op.drop_constraint("uq_user_course_access_user_course", "user_course_access", type_="unique")
    op.drop_index("ix_user_course_access_course_id", table_name="user_course_access")
    op.drop_index("ix_user_course_access_user_id", table_name="user_course_access")
    op.drop_table("user_course_access")
    op.drop_table("tariffs")
    op.drop_column("lessons", "module_id")
    op.drop_table("modules")
    op.drop_index("ix_courses_slug", table_name="courses")
    op.drop_column("courses", "is_legacy")
    op.drop_column("courses", "sort_order")
    op.drop_column("courses", "slug")
