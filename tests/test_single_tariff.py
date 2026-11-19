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
