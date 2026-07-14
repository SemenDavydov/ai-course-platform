"""
Тесты Этапа 2.6: личный кабинет.
Проверяем доступ к разделам, обновление профиля, смену пароля и загрузку аватара.
"""
import io
import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.user_session import UserSession
from app.models.course import Course, Lesson


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def user_with_access(db_session: AsyncSession) -> User:
    """Верифицированный веб-пользователь с доступом к курсу."""
    user = User(
        email="cabinet@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def user_no_access(db_session: AsyncSession) -> User:
    """Верифицированный пользователь БЕЗ доступа к курсу."""
    user = User(
        email="noaccess@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_cookie(db_session: AsyncSession, user_with_access: User) -> str:
    """Сессионный cookie для user_with_access."""
    token = "cabinet_valid_session_token"
    session = UserSession(
        user_id=user_with_access.id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    await db_session.commit()
    return token


@pytest_asyncio.fixture
async def no_access_cookie(db_session: AsyncSession, user_no_access: User) -> str:
    """Сессионный cookie для user_no_access."""
    token = "no_access_session_token"
    session = UserSession(
        user_id=user_no_access.id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    await db_session.commit()
    return token


@pytest_asyncio.fixture
async def published_course(db_session: AsyncSession) -> Course:
    """Опубликованный курс с одним уроком."""
    course = Course(
        title="Тестовый курс",
        description="Описание",
        price=2990.0,
        is_published=True,
    )
    db_session.add(course)
    await db_session.flush()  # получаем id

    lesson = Lesson(
        course_id=course.id,
        title="Урок 1",
        description="Первый урок",
        video_id="test_video_id",
        order=1,
    )
    db_session.add(lesson)
    await db_session.commit()
    await db_session.refresh(course)
    return course


# ---------------------------------------------------------------------------
# Доступ к ЛК: авторизация и has_access
# ---------------------------------------------------------------------------

async def test_cabinet_redirect_to_lessons(client: AsyncClient, auth_cookie: str):
    """/cabinet без слэша → редирект на /cabinet/lessons."""
    response = await client.get(
        "/cabinet",
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/cabinet/lessons"


async def test_lessons_requires_auth(client: AsyncClient):
    """/cabinet/lessons без cookie → редирект на /."""
    response = await client.get("/cabinet/lessons", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/"


async def test_lessons_requires_access(client: AsyncClient, no_access_cookie: str):
    """/cabinet/lessons с is has_access=False → редирект на /?no_access=1."""
    response = await client.get(
        "/cabinet/lessons",
        cookies={"user_session": no_access_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "no_access=1" in response.headers["location"]


async def test_lessons_page_ok(
    client: AsyncClient,
    auth_cookie: str,
    published_course: Course,
):
    """/cabinet/lessons для авторизованного пользователя с доступом → 200."""
    response = await client.get(
        "/cabinet/lessons",
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Урок 1" in response.text


async def test_profile_page_ok(client: AsyncClient, auth_cookie: str):
    """/cabinet/profile → 200 для авторизованного пользователя."""
    response = await client.get(
        "/cabinet/profile",
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Обновление имени
# ---------------------------------------------------------------------------

async def test_update_name(
    client: AsyncClient,
    auth_cookie: str,
    db_session: AsyncSession,
    user_with_access: User,
):
    """POST /cabinet/profile сохраняет имя и редиректит с ?saved=name."""
    response = await client.post(
        "/cabinet/profile",
        data={"name": "Алексей Иванов"},
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=name" in response.headers["location"]

    await db_session.refresh(user_with_access)
    assert user_with_access.name == "Алексей Иванов"


async def test_update_name_empty_clears(
    client: AsyncClient,
    auth_cookie: str,
    db_session: AsyncSession,
    user_with_access: User,
):
    """Пустое имя → name=None."""
    user_with_access.name = "Старое имя"
    await db_session.commit()

    await client.post(
        "/cabinet/profile",
        data={"name": "   "},
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    await db_session.refresh(user_with_access)
    assert user_with_access.name is None


# ---------------------------------------------------------------------------
# Смена пароля
# ---------------------------------------------------------------------------

async def test_change_password_success(
    client: AsyncClient,
    auth_cookie: str,
    db_session: AsyncSession,
    user_with_access: User,
):
    """Корректная смена пароля → 303 с ?saved=password, пароль изменён."""
    response = await client.post(
        "/cabinet/profile/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword456",
            "confirm_password": "newpassword456",
        },
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=password" in response.headers["location"]

    await db_session.refresh(user_with_access)
    assert user_with_access.check_password("newpassword456")


async def test_change_password_wrong_current(client: AsyncClient, auth_cookie: str):
    """Неверный текущий пароль → редирект с ?error=wrong_password."""
    response = await client.post(
        "/cabinet/profile/password",
        data={
            "current_password": "wrongpassword",
            "new_password": "newpassword456",
            "confirm_password": "newpassword456",
        },
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "wrong_password" in response.headers["location"]


async def test_change_password_mismatch(client: AsyncClient, auth_cookie: str):
    """Новый пароль и подтверждение не совпадают → редирект с ?error=password_mismatch."""
    response = await client.post(
        "/cabinet/profile/password",
        data={
            "current_password": "password123",
            "new_password": "newpassword456",
            "confirm_password": "different789",
        },
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "password_mismatch" in response.headers["location"]


async def test_change_password_too_short(client: AsyncClient, auth_cookie: str):
    """Новый пароль короче 8 символов → редирект с ?error=password_too_short."""
    response = await client.post(
        "/cabinet/profile/password",
        data={
            "current_password": "password123",
            "new_password": "abc",
            "confirm_password": "abc",
        },
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "password_too_short" in response.headers["location"]


# ---------------------------------------------------------------------------
# Загрузка аватара
# ---------------------------------------------------------------------------

def _make_jpeg_bytes(size: int = 100) -> bytes:
    """Создаёт минимальный JPEG в памяти через Pillow."""
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (size, size), color=(128, 0, 0))
    img.save(buf, format="JPEG")
    return buf.getvalue()


async def test_avatar_upload_success(
    client: AsyncClient,
    auth_cookie: str,
    db_session: AsyncSession,
    user_with_access: User,
):
    """Загрузка корректного JPEG → редирект с ?saved=avatar, avatar_url обновлён."""
    jpeg_data = _make_jpeg_bytes()

    response = await client.post(
        "/cabinet/profile/avatar",
        files={"avatar": ("avatar.jpg", jpeg_data, "image/jpeg")},
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=avatar" in response.headers["location"]

    await db_session.refresh(user_with_access)
    assert user_with_access.avatar_url is not None
    assert f"/static/uploads/avatars/{user_with_access.id}.jpg" in user_with_access.avatar_url


async def test_avatar_upload_wrong_mime(client: AsyncClient, auth_cookie: str):
    """Загрузка не-изображения → редирект с ?error=bad_type."""
    response = await client.post(
        "/cabinet/profile/avatar",
        files={"avatar": ("doc.pdf", b"%PDF-1.4 test", "application/pdf")},
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "bad_type" in response.headers["location"]


async def test_avatar_upload_too_large(client: AsyncClient, auth_cookie: str):
    """Файл > 5 МБ → редирект с ?error=too_large."""
    big_data = b"x" * (6 * 1024 * 1024)  # 6 МБ
    response = await client.post(
        "/cabinet/profile/avatar",
        files={"avatar": ("big.jpg", big_data, "image/jpeg")},
        cookies={"user_session": auth_cookie},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "too_large" in response.headers["location"]
