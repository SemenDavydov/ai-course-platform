"""
Вход на сайт по одноразовой ссылке из Telegram-бота.
Путь миграции для покупателей из бота, у которых в базе нет email.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.course import Course
from app.services import auth as auth_service
from app.services.access import grant_course_access


@pytest_asyncio.fixture
async def bot_buyer(db_session: AsyncSession) -> User:
    """Покупатель из бота: есть доступ и telegram_id, но нет ни email, ни пароля."""
    course = Course(
        title="Классический",
        description="legacy",
        price=2990,
        is_published=True,
        slug="ai-animations",
        is_legacy=True,
        sort_order=100,
    )
    db_session.add(course)
    await db_session.flush()

    user = User(
        telegram_id=555000111,
        username="bot_buyer",
        email=None,
        password_hash=None,
        has_access=True,
        registration_source="bot_migrated",
    )
    db_session.add(user)
    await db_session.flush()
    await grant_course_access(db_session, user, course.id, "legacy", commit=True)
    await db_session.refresh(user)
    return user


async def test_login_link_opens_cabinet(
    client: AsyncClient,
    db_session: AsyncSession,
    bot_buyer: User,
):
    """Ссылка из бота пускает в кабинет и ставит сессию."""
    token = await auth_service.create_login_token(db_session, bot_buyer)

    response = await client.get(
        f"/auth/telegram-login?token={token}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/cabinet/lessons"
    assert "user_session" in response.cookies

    # Сессия рабочая: кабинет открывается
    cabinet = await client.get(
        "/cabinet/lessons",
        cookies={"user_session": response.cookies["user_session"]},
    )
    assert cabinet.status_code == 200


async def test_login_link_is_single_use(
    client: AsyncClient,
    db_session: AsyncSession,
    bot_buyer: User,
):
    """Повторный переход по той же ссылке не срабатывает."""
    token = await auth_service.create_login_token(db_session, bot_buyer)

    first = await client.get(f"/auth/telegram-login?token={token}", follow_redirects=False)
    assert first.status_code == 303
    assert first.headers["location"] == "/cabinet/lessons"

    second = await client.get(f"/auth/telegram-login?token={token}", follow_redirects=False)
    assert second.status_code == 303
    assert second.headers["location"] == "/?auth_error=login_link_expired"


async def test_expired_login_link_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    bot_buyer: User,
):
    """Просроченная ссылка (>15 минут) не пускает."""
    token = await auth_service.create_login_token(db_session, bot_buyer)

    bot_buyer.login_token_sent_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    db_session.add(bot_buyer)
    await db_session.commit()

    response = await client.get(f"/auth/telegram-login?token={token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login_link_expired"


async def test_unknown_login_token_rejected(client: AsyncClient):
    """Выдуманный токен не пускает."""
    response = await client.get(
        "/auth/telegram-login?token=totally-made-up",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login_link_expired"


async def test_blocked_user_cannot_use_login_link(
    client: AsyncClient,
    db_session: AsyncSession,
    bot_buyer: User,
):
    """Заблокированному пользователю ссылка не помогает."""
    token = await auth_service.create_login_token(db_session, bot_buyer)

    bot_buyer.is_blocked = True
    db_session.add(bot_buyer)
    await db_session.commit()

    response = await client.get(f"/auth/telegram-login?token={token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?auth_error=login_link_expired"
