"""Продаётся только «Самостоятельный»: Pro/VIP выключены, продажи без расписания."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course, Tariff
from app.services.site_settings import set_landing_mode


@pytest.mark.asyncio
async def test_landing_with_only_self_tariff(client, db_session: AsyncSession):
    course = Course(title="AI STORY", description="d", price=8990, is_published=True,
                    slug="ai-story", sort_order=0, is_legacy=False)
    db_session.add(course)
    await db_session.flush()
    db_session.add_all([
        Tariff(course_id=course.id, slug="self", name="Самостоятельный", price=8990, is_active=True, sort_order=-1),
        Tariff(course_id=course.id, slug="pro", name="Pro", price=14990, is_active=False),
        Tariff(course_id=course.id, slug="vip", name="VIP", price=29990, is_active=False, sort_order=1),
    ])
    await db_session.commit()
    await set_landing_mode(db_session, "sales")

    html = (await client.get("/")).text
    assert "Купить Самостоятельный" in html
    assert "Купить Pro" not in html and "Купить VIP" not in html
    assert "Рассрочка на тарифы Pro и VIP" not in html  # нечего оформлять в рассрочку
    assert "max-w-md" in html and "lg:grid-cols-3" not in html
    assert "Отправить заявку" not in html  # предзаписи нет


@pytest.mark.asyncio
async def test_waitlist_rejected_in_sales_mode(client, db_session: AsyncSession):
    from sqlalchemy import func, select

    from app.models.site import WaitlistApplication

    await set_landing_mode(db_session, "sales")
    r = await client.post("/waitlist", data={
        "name": "spam", "phone": "1", "email": "spam@example.com", "accepted_privacy": "on",
    })
    assert r.status_code == 303
    assert await db_session.scalar(select(func.count()).select_from(WaitlistApplication)) == 0


# --- Шаг 2: защита форм ------------------------------------------------------
from unittest.mock import AsyncMock, MagicMock

from app.models.site import WaitlistApplication
from app.models.user import User
from app.models.user_session import UserSession


async def _count(db):
    from sqlalchemy import func, select
    return await db.scalar(select(func.count()).select_from(WaitlistApplication))


@pytest.mark.asyncio
@pytest.mark.parametrize("extra, saved", [
    ({}, 1),                                                         # обычный человек
    ({"website": "http://spam.example"}, 0),                         # ловушка сработала
    ({"name": "* * * test message 1: https://myxa.cc/ * * *"}, 0),   # ровно тот спам
    ({"name": '<a href="https://myxa.cc/">message 2</a>'}, 0),
    ({"social": "www.spam.example"}, 0),
    ({"phone": "624763197981abc"}, 0),
    ({"email": "2ry~e95v0d4d2y@"}, 0),
])
async def test_waitlist_filters_bots(client, db_session, extra, saved):
    await set_landing_mode(db_session, "enrollment")
    form = {"name": "Анна", "phone": "+7 999 123-45-67", "email": "anna@example.com",
            "social": "@anna", "accepted_privacy": "on", **extra}
    r = await client.post("/waitlist", data=form)
    assert r.status_code == 303
    assert await _count(db_session) == saved
    if extra.get("website"):
        assert "waitlist=ok" in r.headers["location"]  # бот не узнаёт, что его отсеяли


@pytest.mark.asyncio
async def test_landing_user_json_cannot_break_out_of_script(client, db_session):
    from datetime import datetime, timedelta, timezone

    evil = User(email="x</script><script>alert(1)</script>@e.ru", registration_source="bot_migrated")
    db_session.add(evil)
    await db_session.flush()
    db_session.add(UserSession(user_id=evil.id, session_token="evil",
                               expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db_session.commit()
    client.cookies.set("user_session", "evil")
    html = (await client.get("/")).text
    assert "<script>alert(1)</script>" not in html
    assert "\u003c/script\u003e" in html


@pytest.mark.asyncio
async def test_bot_rejects_malformed_email(db_session):
    from app.bot import bot as bot_module

    msg = MagicMock()
    msg.from_user.id = 4242
    msg.from_user.username = msg.from_user.first_name = msg.from_user.last_name = None
    msg.text = "x</script>@e.ru"
    msg.answer = AsyncMock()

    class S:
        async def get_data(self): return {}
        async def clear(self): pass

    await bot_module.process_email(msg, S(), db_session)
    assert "корректный email" in msg.answer.await_args.args[0]
