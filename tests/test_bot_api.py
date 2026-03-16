import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.models.course import Course, Lesson


@pytest.mark.asyncio
async def test_get_user(client: AsyncClient, db_session: AsyncSession):
    # Создаем тестового пользователя
    user = User(
        telegram_id=123456789,
        username="test_user",
        has_access=True
    )
    db_session.add(user)
    await db_session.commit()

    response = await client.get("/api/v1/bot/user/123456789")

    assert response.status_code == 200
    data = response.json()
    assert data["telegram_id"] == 123456789
    assert data["username"] == "test_user"
    assert data["has_access"] == True


@pytest.mark.asyncio
async def test_get_course(client: AsyncClient, db_session: AsyncSession):
    # Создаем тестовый курс
    course = Course(
        title="Test Course",
        description="Test Description",
        price=5000,
        is_published=True
    )
    db_session.add(course)
    await db_session.flush()

    # Добавляем уроки
    lesson1 = Lesson(
        course_id=course.id,
        title="Lesson 1",
        video_id="video1",
        order=1
    )
    lesson2 = Lesson(
        course_id=course.id,
        title="Lesson 2",
        video_id="video2",
        order=2
    )
    db_session.add_all([lesson1, lesson2])
    await db_session.commit()

    response = await client.get("/api/v1/bot/course")

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Course"
    assert len(data["lessons"]) == 2
    assert data["lessons"][0]["title"] == "Lesson 1"