import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.models.course import Course, Lesson


@pytest.mark.asyncio
async def test_unauthenticated_user_endpoints_removed(
    client: AsyncClient, db_session: AsyncSession
):
    """Публичные эндпоинты по telegram_id удалены (утечка email / угон аккаунта)."""
    user = User(
        telegram_id=123456789,
        username="test_user",
        email="victim@example.com",
        has_access=True,
    )
    db_session.add(user)
    await db_session.commit()

    r = await client.get("/api/v1/bot/user/123456789")
    assert r.status_code == 404
    assert "victim@example.com" not in r.text

    r = await client.post(
        "/api/v1/bot/user/123456789/email", params={"email": "attacker@example.com"}
    )
    assert r.status_code in (404, 405)
    await db_session.refresh(user)
    assert user.email == "victim@example.com"

    r = await client.get(
        "/api/v1/bot/lesson/1/video", params={"telegram_id": 123456789}
    )
    assert r.status_code == 404


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
    # Kinescope ID не должны утекать в публичный API
    assert all("video_id" not in lesson for lesson in data["lessons"])
    assert "video1" not in response.text

@pytest.mark.asyncio
async def test_video_page_checks_course_access(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """JWT-ссылка на видео не должна открывать урок курса, который не куплен."""
    from app.services.access import grant_course_access
    from app.services.video import VideoService

    async def fake_embed(self, video_id):
        return f"https://kinescope.io/embed/{video_id}"

    monkeypatch.setattr(VideoService, "get_embed_link", fake_embed)

    paid = Course(title="Paid", description="d", price=1, is_published=True, slug="paid")
    other = Course(title="Other", description="d", price=1, is_published=True, slug="other")
    db_session.add_all([paid, other])
    await db_session.flush()
    db_session.add_all([
        Lesson(course_id=paid.id, title="P1", video_id="vid_paid", order=1),
        Lesson(course_id=other.id, title="O1", video_id="vid_other", order=1),
    ])
    user = User(telegram_id=555, username="buyer", email="buyer@example.com")
    db_session.add(user)
    await db_session.flush()
    await grant_course_access(db_session, user, paid.id, "pro")
    await db_session.commit()

    svc = VideoService()
    own_link = await svc.generate_jwt_link(user, "vid_paid")
    foreign_link = await svc.generate_jwt_link(user, "vid_other")

    own = await client.get("/api/v1/bot/video/vid_paid?" + own_link.split("?", 1)[1])
    assert own.status_code == 200
    assert "kinescope.io/embed/vid_paid" in own.text

    foreign = await client.get("/api/v1/bot/video/vid_other?" + foreign_link.split("?", 1)[1])
    assert foreign.status_code == 200
    assert "Доступ запрещён" in foreign.text
    assert "kinescope.io/embed/vid_other" not in foreign.text
