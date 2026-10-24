"""Уроки открываются купившим в период продаж только по его окончании."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.admin_session import AdminSession
from app.models.course import Course, Lesson, Module, Tariff
from app.models.material import Material
from app.models.payment import Payment
from app.models.user import User
from app.models.user_session import UserSession
from app.services.access import (
    get_access,
    grant_course_access,
    lessons_opening_at,
    reschedule_pending_openings,
    user_can_watch,
    user_has_course,
)
from app.services.site_settings import get_sales_schedule, set_sales_schedule
from app.services.tariffs import SELF_ACCESS_DAYS


@pytest_asyncio.fixture
async def course(db_session: AsyncSession) -> Course:
    c = Course(title="AI STORY", description="d", price=14990, is_published=True,
               slug="ai-story", sort_order=0, is_legacy=False)
    db_session.add(c)
    await db_session.flush()
    db_session.add_all([
        Tariff(course_id=c.id, slug="self", name="Самостоятельный", price=8990, is_active=True, sort_order=-1),
        Tariff(course_id=c.id, slug="pro", name="Pro", price=14990, is_active=True, sort_order=0),
        Tariff(course_id=c.id, slug="vip", name="VIP", price=29990, is_active=True, sort_order=1),
    ])
    m = Module(course_id=c.id, title="М1", order=1)
    db_session.add(m)
    await db_session.flush()
    lesson = Lesson(course_id=c.id, module_id=m.id, title="Урок 1", order=101, video_id="vid1")
    db_session.add(lesson)
    await db_session.flush()
    db_session.add(Material(lesson_id=lesson.id, title="PDF", file_name="f.pdf",
                            original_name="f.pdf", file_type="application/pdf", file_size=1))
    await db_session.commit()
    return c


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    u = User(email="buyer@example.com", telegram_id=4040, registration_source="web", accepted_offer=True)
    db_session.add(u)
    await db_session.commit()
    return u


async def _open_window(db, *, ends_in=timedelta(days=3)):
    now = datetime.now(timezone.utc)
    return await set_sales_schedule(db, enabled=True, start=now - timedelta(days=1), end=now + ends_in)


async def _login(client, db, user):
    db.add(UserSession(user_id=user.id, session_token=f"s{user.id}",
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db.commit()
    client.cookies.set("user_session", f"s{user.id}")


async def _pay_via_webhook(client, db, user, course, slug, price, yookassa_api, pid="yk_open_1"):
    tariff = (await db.execute(select(Tariff).where(Tariff.course_id == course.id, Tariff.slug == slug))).scalar_one()
    db.add(Payment(user_id=user.id, amount=price, payment_id=pid, status="pending",
                   course_id=course.id, tariff_id=tariff.id, tariff_slug=slug, provider="yookassa"))
    await db.commit()
    yookassa_api.set_payment(amount=f"{price:.2f}")
    with patch("app.tasks.send_email") as mail, patch("app.api.webhooks.bot.send_message", new=AsyncMock()) as tg:
        r = await client.post("/webhooks/yookassa", json={
            "event": "payment.succeeded",
            "object": {"id": pid, "status": "succeeded", "amount": {"value": f"{price:.2f}", "currency": "RUB"}},
        })
    assert r.status_code == 200, r.text
    return mail, tg


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_purchase_in_window_opens_at_sales_end(client, db_session, course, user, yookassa_api):
    schedule = await _open_window(db_session)
    mail, _ = await _pay_via_webhook(client, db_session, user, course, "self", 8990, yookassa_api)

    access = await get_access(db_session, user.id, course.id)
    await db_session.refresh(access)
    assert _utc(access.opens_at) == schedule.end
    # 60 дней «Самостоятельного» — с момента открытия уроков
    assert _utc(access.expires_at) == schedule.end + timedelta(days=SELF_ACCESS_DAYS)
    assert await user_has_course(db_session, user, course.id)       # курс куплен
    assert not await user_can_watch(db_session, user, course.id)    # но уроки закрыты

    html = mail.call_args.args[2]
    assert "Уроки откроются" in html and "Доступ к урокам уже открыт" not in html
    assert "с момента открытия уроков" in html


@pytest.mark.asyncio
async def test_telegram_notification_mentions_opening(client, db_session, course, yookassa_api, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ENABLED", True)
    tg_user = User(telegram_id=5050, email="tg@example.com", registration_source="bot_migrated")
    db_session.add(tg_user)
    await db_session.commit()
    await _open_window(db_session)
    _, tg = await _pay_via_webhook(client, db_session, tg_user, course, "pro", 14990, yookassa_api, "yk_tg_open")
    text = tg.call_args.kwargs["text"]
    assert "🔒 Уроки откроются" in text and "Уроки уже доступны" not in text


@pytest.mark.asyncio
async def test_locked_everywhere_until_opening(client, db_session, course, user):
    schedule = await _open_window(db_session)
    await grant_course_access(db_session, user, course.id, "pro", opens_at=schedule.end, commit=True)
    lesson = (await db_session.execute(select(Lesson).where(Lesson.course_id == course.id))).scalar_one()
    material = (await db_session.execute(select(Material))).scalar_one()
    await _login(client, db_session, user)

    page = (await client.get("/cabinet/lessons")).text
    assert "До открытия уроков осталось" in page
    assert "fa-lock" in page
    assert f'href="/cabinet/lessons/{lesson.id}"' not in page

    r = await client.get(f"/cabinet/lessons/{lesson.id}", follow_redirects=False)
    assert r.status_code == 302 and "/cabinet/lessons?course_id=" in r.headers["location"]

    assert (await client.get(f"/api/v1/materials/{material.id}/download")).status_code == 403
    assert (await client.post("/api/v1/progress", json={"lesson_id": lesson.id, "percent_watched": 10})).status_code == 403

    landing = (await client.get("/")).text
    assert "Уроки откроются" in landing


@pytest.mark.asyncio
async def test_bot_lesson_locked_message(db_session, course, user):
    from app.bot import bot as bot_module

    schedule = await _open_window(db_session)
    await grant_course_access(db_session, user, course.id, "self", opens_at=schedule.end, commit=True)
    lesson = (await db_session.execute(select(Lesson))).scalar_one()
    cb = MagicMock()
    cb.data = f"lesson_{lesson.id}"
    cb.from_user.id = user.telegram_id
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    await bot_module.process_lesson(cb, db_session)
    text = cb.message.edit_text.await_args.args[0]
    assert "Уроки откроются" in text and "kinescope" not in text


@pytest.mark.asyncio
async def test_opens_after_sales_end(db_session, course, user):
    await grant_course_access(db_session, user, course.id, "pro",
                              opens_at=datetime.now(timezone.utc) + timedelta(hours=1), commit=True)
    access = await get_access(db_session, user.id, course.id)
    access.opens_at = datetime.now(timezone.utc) - timedelta(seconds=1)  # время прошло
    await db_session.commit()
    assert lessons_opening_at(access) is None
    assert await user_can_watch(db_session, user, course.id)


@pytest.mark.asyncio
async def test_existing_students_not_locked(client, db_session, course, user, yookassa_api):
    # Ученик купил раньше (opens_at NULL), потом открылся период продаж.
    await grant_course_access(db_session, user, course.id, "self", commit=True)
    await _open_window(db_session)
    assert await user_can_watch(db_session, user, course.id)
    # Апгрейд действующего ученика во время продаж уроки не закрывает.
    db_session.add(Payment(user_id=user.id, amount=8990, payment_id="old_self", status="succeeded",
                           course_id=course.id, tariff_slug="self", provider="yookassa"))
    await db_session.commit()
    await _pay_via_webhook(client, db_session, user, course, "pro", 6000, yookassa_api, "yk_upgrade")
    access = await get_access(db_session, user.id, course.id)
    await db_session.refresh(access)
    assert access.tariff_slug == "pro" and access.opens_at is None
    assert await user_can_watch(db_session, user, course.id)


@pytest.mark.asyncio
async def test_no_lock_without_schedule_or_admin_grant(client, db_session, course, user, yookassa_api):
    await _pay_via_webhook(client, db_session, user, course, "pro", 14990, yookassa_api, "yk_noschedule")
    assert await user_can_watch(db_session, user, course.id)

    other = User(email="gift@example.com", registration_source="web")
    db_session.add(other)
    await db_session.commit()
    await _open_window(db_session)
    await grant_course_access(db_session, other, course.id, "pro", commit=True)  # выдача из админки
    assert await user_can_watch(db_session, other, course.id)


@pytest.mark.asyncio
async def test_reschedule_moves_opening_and_self_expiry(db_session, course, user):
    schedule = await _open_window(db_session)
    await grant_course_access(db_session, user, course.id, "self", opens_at=schedule.end, commit=True)
    new_end = schedule.end + timedelta(days=2)
    assert await reschedule_pending_openings(db_session, new_end) == 1
    await db_session.commit()
    access = await get_access(db_session, user.id, course.id)
    assert _utc(access.opens_at) == new_end
    assert _utc(access.expires_at) == new_end + timedelta(days=SELF_ACCESS_DAYS)

    # Расписание выключили — уроки открываются сразу, срок от сегодняшнего дня.
    before = datetime.now(timezone.utc)
    assert await reschedule_pending_openings(db_session, None) == 1
    await db_session.commit()
    assert access.opens_at is None
    assert abs((_utc(access.expires_at) - (before + timedelta(days=SELF_ACCESS_DAYS))).total_seconds()) < 60


@pytest.mark.asyncio
async def test_admin_schedule_save_reschedules(client, db_session, course, user):
    schedule = await _open_window(db_session)
    await grant_course_access(db_session, user, course.id, "pro", opens_at=schedule.end, commit=True)
    admin = User(username="boss", email="boss@example.com", role="admin", is_admin=True, registration_source="web")
    db_session.add(admin)
    await db_session.flush()
    db_session.add(AdminSession(user_id=admin.id, session_token="adm",
                                expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db_session.commit()
    client.cookies.set("admin_session", "adm")

    r = await client.post("/admin/settings/sales-schedule", data={
        "enabled": "1", "sales_start": "2026-01-01T00:00", "sales_end": "2099-01-01T12:00",
    })
    assert r.status_code == 303
    access = await get_access(db_session, user.id, course.id)
    await db_session.refresh(access)
    assert _utc(access.opens_at) == (await get_sales_schedule(db_session)).end

    html = (await client.get(f"/admin/users/{user.id}")).text
    assert "🔒 уроки с" in html
