"""
Тесты Этапа 2.6: API прогресса просмотра уроков.
Проверяем апсерт, монотонный рост, флаг is_completed, GET прогресса.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.user_session import UserSession
from app.models.course import Course, Lesson
from app.models.lesson_progress import LessonProgress
from app.services.access import grant_course_access


@pytest_asyncio.fixture
async def lesson(db_session: AsyncSession) -> Lesson:
    """Урок в опубликованном курсе."""
    course = Course(
        title="Тестовый курс",
        description="Описание",
        price=2990.0,
        is_published=True,
        slug="progress-course",
    )
    db_session.add(course)
    await db_session.flush()

    lesson = Lesson(
        course_id=course.id,
        title="Тестовый урок",
        description="Описание урока",
        video_id="test_video_id",
        order=1,
    )
    db_session.add(lesson)
    await db_session.commit()
    await db_session.refresh(lesson)
    return lesson


@pytest_asyncio.fixture
async def user_with_access(db_session: AsyncSession, lesson: Lesson) -> User:
    """Пользователь с доступом к курсу."""
    user = User(
        email="progress@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()
    await grant_course_access(db_session, user, lesson.course_id, "pro", commit=True)
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def user_no_access(db_session: AsyncSession) -> User:
    """Пользователь БЕЗ доступа к курсу."""
    user = User(
        email="noaccess_progress@test.com",
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
    token = "progress_valid_session_token"
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
    token = "no_access_progress_session_token"
    session = UserSession(
        user_id=user_no_access.id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    await db_session.commit()
    return token


# ---------------------------------------------------------------------------
# POST /api/v1/progress
# ---------------------------------------------------------------------------

async def test_progress_create(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
    db_session: AsyncSession,
    user_with_access: User,
):
    """Первая запись прогресса создаётся корректно."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 50},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["lesson_id"] == lesson.id
    assert data["percent_watched"] == 50
    assert data["is_completed"] is False

    # Проверяем в БД
    result = await db_session.execute(
        select(LessonProgress).where(
            LessonProgress.user_id == user_with_access.id,
            LessonProgress.lesson_id == lesson.id,
        )
    )
    progress = result.scalar_one()
    assert progress.percent_watched == 50
    assert progress.is_completed is False


async def test_progress_update_increases(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
    db_session: AsyncSession,
    user_with_access: User,
):
    """Прогресс обновляется, если новое значение больше."""
    # Первый запрос
    await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 30},
        cookies={"user_session": auth_cookie},
    )
    # Второй — выше
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 60},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    assert response.json()["percent_watched"] == 60


async def test_progress_monotonic_no_decrease(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
    db_session: AsyncSession,
    user_with_access: User,
):
    """Прогресс не снижается: если пришло меньшее значение — игнорируется."""
    await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 70},
        cookies={"user_session": auth_cookie},
    )
    # Попытка «откатить» назад
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 20},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    # Значение в БД не изменилось
    result = await db_session.execute(
        select(LessonProgress).where(
            LessonProgress.user_id == user_with_access.id,
            LessonProgress.lesson_id == lesson.id,
        )
    )
    progress = result.scalar_one()
    assert progress.percent_watched == 70


async def test_progress_completion_flag_at_90(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
    db_session: AsyncSession,
    user_with_access: User,
):
    """При percent_watched >= 90 → is_completed = True."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 90},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    assert response.json()["is_completed"] is True

    result = await db_session.execute(
        select(LessonProgress).where(
            LessonProgress.user_id == user_with_access.id,
            LessonProgress.lesson_id == lesson.id,
        )
    )
    assert result.scalar_one().is_completed is True


async def test_progress_completion_not_set_below_90(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
):
    """При percent_watched < 90 → is_completed = False."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 89},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    assert response.json()["is_completed"] is False


async def test_progress_completion_at_100(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
):
    """100% → is_completed = True."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 100},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    assert response.json()["is_completed"] is True


async def test_progress_requires_access(
    client: AsyncClient,
    no_access_cookie: str,
    lesson: Lesson,
):
    """Без доступа к курсу → 403."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 50},
        cookies={"user_session": no_access_cookie},
    )
    assert response.status_code == 403


async def test_progress_requires_auth(client: AsyncClient, lesson: Lesson):
    """Без сессии → 401."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 50},
    )
    assert response.status_code == 401


async def test_progress_nonexistent_lesson(client: AsyncClient, auth_cookie: str):
    """Несуществующий lesson_id → 404."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": 99999, "percent_watched": 50},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 404


async def test_progress_invalid_percent(client: AsyncClient, auth_cookie: str, lesson: Lesson):
    """percent_watched > 100 → 422."""
    response = await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 150},
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/progress
# ---------------------------------------------------------------------------

async def test_get_progress_empty(client: AsyncClient, auth_cookie: str):
    """Пользователь без прогресса → пустой массив."""
    response = await client.get(
        "/api/v1/progress",
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_get_progress_returns_data(
    client: AsyncClient,
    auth_cookie: str,
    lesson: Lesson,
):
    """После POST прогресса GET возвращает запись."""
    await client.post(
        "/api/v1/progress",
        json={"lesson_id": lesson.id, "percent_watched": 45},
        cookies={"user_session": auth_cookie},
    )

    response = await client.get(
        "/api/v1/progress",
        cookies={"user_session": auth_cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["lesson_id"] == lesson.id
    assert data[0]["percent_watched"] == 45


async def test_get_progress_requires_auth(client: AsyncClient):
    """GET /api/v1/progress без сессии → 401."""
    response = await client.get("/api/v1/progress")
    assert response.status_code == 401
