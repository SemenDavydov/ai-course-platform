import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


@pytest.mark.asyncio
async def test_admin_register_disabled(client: AsyncClient, db_session: AsyncSession):
    """Регистрация админа по общему коду отключена (раньше дефолтный код был "admin")."""
    r = await client.get("/admin/register")
    assert r.status_code == 404

    for code in ("admin", "admin123"):
        r = await client.post(
            "/admin/register",
            data={
                "username": f"hacker_{code}",
                "email": f"{code}@example.com",
                "password": "password123",
                "admin_code": code,
            },
        )
        assert r.status_code in (404, 405)

    result = await db_session.execute(select(User).where(User.username.like("hacker_%")))
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_docs_disabled_unless_debug():
    from app.config import settings
    from app.main import app

    if settings.DEBUG:
        assert app.docs_url == "/docs"
    else:
        assert app.docs_url is None and app.openapi_url is None


@pytest.mark.asyncio
async def test_security_headers(client: AsyncClient):
    r = await client.get("/offer")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_session_middleware_does_not_use_admin_cookie_name():
    from starlette.middleware.sessions import SessionMiddleware
    from app.main import app

    mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    assert mw.kwargs["session_cookie"] != "admin_session"


@pytest.mark.asyncio
async def test_lesson_materials_list_requires_access(
    client: AsyncClient, db_session: AsyncSession
):
    from app.models.course import Course, Lesson
    from app.models.material import Material
    from app.services.access import grant_course_access

    course = Course(title="C", description="d", price=1, is_published=True, slug="c")
    db_session.add(course)
    await db_session.flush()
    lesson = Lesson(course_id=course.id, title="L", video_id="v", order=1)
    db_session.add(lesson)
    await db_session.flush()
    db_session.add(Material(
        lesson_id=lesson.id, title="Секрет", file_name="secret.pdf", original_name="Секрет.pdf",
        file_type="application/pdf", file_size=1,
    ))
    buyer = User(email="buyer@example.com", registration_source="web", accepted_offer=True)
    buyer.set_password("password123")
    stranger = User(email="stranger@example.com", registration_source="web", accepted_offer=True)
    stranger.set_password("password123")
    db_session.add_all([buyer, stranger])
    await db_session.flush()
    await grant_course_access(db_session, buyer, course.id, "pro")
    await db_session.commit()

    url = f"/api/v1/materials/lesson/{lesson.id}"
    assert (await client.get(url)).status_code == 403  # аноним

    await client.post("/auth/login", json={"email": "stranger@example.com", "password": "password123"})
    assert (await client.get(url)).status_code == 403  # без покупки
    await client.post("/auth/logout")
    client.cookies.clear()

    await client.post("/auth/login", json={"email": "buyer@example.com", "password": "password123"})
    r = await client.get(url)
    assert r.status_code == 200
    assert r.json()[0]["original_name"] == "Секрет.pdf"
