"""
Скачивание материалов урока из личного кабинета.
"""
import os

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course, Lesson
from app.models.material import Material
from app.models.user import User
from app.models.user_session import UserSession
from app.services.access import grant_course_access

MATERIALS_DIR = os.path.join("uploads", "materials")


@pytest_asyncio.fixture
async def material(db_session: AsyncSession) -> Material:
    """Материал урока + реальный файл на диске."""
    course = Course(
        title="Курс",
        description="-",
        price=2990.0,
        is_published=True,
        slug="mat-course",
    )
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)

    lesson = Lesson(course_id=course.id, title="Урок 1", order=1, video_id="test-video-id")
    db_session.add(lesson)
    await db_session.commit()
    await db_session.refresh(lesson)

    os.makedirs(MATERIALS_DIR, exist_ok=True)
    file_name = "test_material.txt"
    path = os.path.join(MATERIALS_DIR, file_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("шаблон промта")

    item = Material(
        lesson_id=lesson.id,
        title="Шаблоны промтов",
        file_name=file_name,
        original_name="Шаблоны промтов.txt",
        file_size=os.path.getsize(path),
        file_type="text/plain",
        downloads_count=0,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    item._course_id = course.id  # type: ignore

    yield item

    if os.path.exists(path):
        os.remove(path)


async def _session_token(db_session: AsyncSession, user: User) -> str:
    token = f"materials_session_{user.id}"
    db_session.add(
        UserSession(
            user_id=user.id,
            session_token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    await db_session.commit()
    return token


@pytest_asyncio.fixture
async def buyer(db_session: AsyncSession, material: Material) -> User:
    user = User(
        email="materials_buyer@test.com",
        has_access=True,
        registration_source="web",
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()
    await grant_course_access(
        db_session, user, material._course_id, "pro", commit=True  # type: ignore
    )
    await db_session.refresh(user)
    return user


async def test_download_with_session_cookie(
    client: AsyncClient,
    db_session: AsyncSession,
    buyer: User,
    material: Material,
):
    """Пользователь с доступом скачивает материал по обычной ссылке из кабинета."""
    token = await _session_token(db_session, buyer)

    response = await client.get(
        f"/api/v1/materials/{material.id}/download",
        cookies={"user_session": token},
    )
    assert response.status_code == 200
    assert "шаблон промта" in response.text

    await db_session.refresh(material)
    assert material.downloads_count == 1


async def test_download_without_auth_denied(
    client: AsyncClient,
    material: Material,
):
    """Без сессии и без токена — 403, а не 422."""
    response = await client.get(f"/api/v1/materials/{material.id}/download")
    assert response.status_code == 403


async def test_download_denied_without_course_access(
    client: AsyncClient,
    db_session: AsyncSession,
    material: Material,
):
    """Зарегистрирован, но курс не куплен — материалы не отдаём."""
    user = User(email="no_access@test.com", has_access=False, registration_source="web")
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = await _session_token(db_session, user)

    response = await client.get(
        f"/api/v1/materials/{material.id}/download",
        cookies={"user_session": token},
    )
    assert response.status_code == 403
