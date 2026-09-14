"""sync AI STORY module and lesson titles to the new program

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-14

"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None

# order, title, lesson titles
PROGRAM = [
    (
        1,
        "Введение в профессию",
        [
            "Как устроен мир ИИ-контента в 2026 году",
            "Обзор инструментов",
            "Как оплатить зарубежные подписки",
        ],
    ),
    (
        2,
        "Идеи и сценарии вирусных ИИ-сериалов",
        [
            "Всё о сценариях: теория",
            "Формула вирусной серии",
            "Создание сценария: практика",
        ],
    ),
    (
        3,
        "Работа с генерацией в 2Д и 3Д стилях",
        [
            "Знакомство с Grok",
            "Создание героев в разных стилях",
            "Работа с раскадровками на примере Матриархат-Прайм",
            "Генерация видео для сериала Матриархат-Прайм",
            "Основы видеомонтажа для нейросериала",
            "Написание сценария для 2D сказки",
            "Создание стильных 2D раскадровок для сказки",
            "Работа с Seedance 2.5 и Grok: создание кинематографичных кадров для сказки",
            "Работа в ElevenLabs: создание голоса и озвучки",
            "Видеомонтаж 2D сказки",
            "Мини-урок: работа в Kling",
        ],
    ),
    (
        4,
        "Работа с реализмом",
        [
            "Основы генерации в Nano Banana",
            "Создание фото в стиле реализм",
            "Генерация видео в стиле реализм",
            "Монтаж видео в стиле реализм",
        ],
    ),
    (
        5,
        "ИИ контент для коммерции: теория",
        [
            "Как стать заметным для клиентов и брендов",
            "Как создать рекламу: разбор кейса Матриархат-Прайм",
            "Виды интеграций",
        ],
    ),
    (
        6,
        "ИИ контент для коммерции: практика",
        [
            "Продуктовая съёмка: от идеи до результата",
            "Модельная съёмка",
        ],
    ),
    (
        7,
        "Монетизация и продвижение",
        [
            "Доход в ИИ бизнесе",
            "Портфолио ИИ креатора",
            "Продвижение",
            "База документов",
        ],
    ),
]

PREVIOUS = [
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


def _lesson_is_empty(conn, lesson_id: int, video_id: str | None) -> bool:
    if video_id and video_id not in ("", "pending"):
        return False
    materials = conn.execute(
        sa.text("SELECT 1 FROM materials WHERE lesson_id = :id LIMIT 1"),
        {"id": lesson_id},
    ).first()
    if materials:
        return False
    progress = conn.execute(
        sa.text("SELECT 1 FROM lesson_progress WHERE lesson_id = :id LIMIT 1"),
        {"id": lesson_id},
    ).first()
    return progress is None


def _sync(program: list) -> None:
    conn = op.get_bind()
    course = conn.execute(
        sa.text("SELECT id FROM courses WHERE slug = 'ai-story' LIMIT 1")
    ).first()
    if course is None:
        return
    course_id = course[0]

    for mod_order, mod_title, lesson_titles in program:
        module = conn.execute(
            sa.text(
                """
                SELECT id FROM modules
                WHERE course_id = :course_id AND "order" = :ord
                LIMIT 1
                """
            ),
            {"course_id": course_id, "ord": mod_order},
        ).first()
        if module is None:
            module = conn.execute(
                sa.text(
                    """
                    INSERT INTO modules (course_id, title, "order", button_label)
                    VALUES (:course_id, :title, :ord, :btn)
                    RETURNING id
                    """
                ),
                {
                    "course_id": course_id,
                    "title": mod_title,
                    "ord": mod_order,
                    "btn": f"Модуль {mod_order}",
                },
            ).first()
        else:
            conn.execute(
                sa.text(
                    """
                    UPDATE modules
                    SET title = :title, button_label = :btn
                    WHERE id = :id
                    """
                ),
                {"title": mod_title, "btn": f"Модуль {mod_order}", "id": module[0]},
            )
        module_id = module[0]

        lessons = conn.execute(
            sa.text(
                """
                SELECT id, video_id FROM lessons
                WHERE module_id = :module_id
                ORDER BY "order", id
                """
            ),
            {"module_id": module_id},
        ).fetchall()

        for index, lesson_title in enumerate(lesson_titles, start=1):
            lesson_order = mod_order * 100 + index
            description = f"Модуль {mod_order}. Урок {index}: {lesson_title}"
            if index <= len(lessons):
                conn.execute(
                    sa.text(
                        """
                        UPDATE lessons
                        SET title = :title,
                            description = :description,
                            "order" = :ord,
                            module_id = :module_id,
                            course_id = :course_id
                        WHERE id = :id
                        """
                    ),
                    {
                        "title": lesson_title,
                        "description": description,
                        "ord": lesson_order,
                        "module_id": module_id,
                        "course_id": course_id,
                        "id": lessons[index - 1][0],
                    },
                )
            else:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO lessons
                            (course_id, module_id, title, description, video_id, "order")
                        VALUES
                            (:course_id, :module_id, :title, :description, 'pending', :ord)
                        """
                    ),
                    {
                        "course_id": course_id,
                        "module_id": module_id,
                        "title": lesson_title,
                        "description": description,
                        "ord": lesson_order,
                    },
                )

        for extra_id, video_id in lessons[len(lesson_titles):]:
            if _lesson_is_empty(conn, extra_id, video_id):
                conn.execute(
                    sa.text("DELETE FROM lessons WHERE id = :id"),
                    {"id": extra_id},
                )
            else:
                conn.execute(
                    sa.text(
                        """
                        UPDATE lessons
                        SET module_id = NULL,
                            title = CASE
                                WHEN title LIKE 'Архив:%' THEN title
                                ELSE 'Архив: ' || title
                            END
                        WHERE id = :id
                        """
                    ),
                    {"id": extra_id},
                )


def upgrade() -> None:
    _sync(PROGRAM)


def downgrade() -> None:
    _sync(PREVIOUS)
