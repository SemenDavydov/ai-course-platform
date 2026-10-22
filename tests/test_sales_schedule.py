"""C3: расписание продаж — фазы, режим лендинга, закрытие новых покупок, админка."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_session import AdminSession
from app.models.course import Course, Tariff
from app.models.user import User
from app.models.user_session import UserSession
from app.services.access import grant_course_access
from app.services.site_settings import (
    MSK,
    PHASE_BEFORE,
    PHASE_CLOSED,
    PHASE_OPEN,
    SalesSchedule,
    effective_landing_mode,
    get_sales_schedule,
    new_sales_closed_reason,
    set_landing_mode,
    set_sales_schedule,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def story_course(db_session: AsyncSession) -> Course:
    course = Course(title="AI STORY", description="d", price=14990, is_published=True,
                    slug="ai-story", sort_order=0, is_legacy=False)
    db_session.add(course)
    await db_session.flush()
    db_session.add_all([
        Tariff(course_id=course.id, slug="self", name="Самостоятельный", price=8990,
               is_active=True, sort_order=-1),
        Tariff(course_id=course.id, slug="pro", name="Pro", price=14990, is_active=True, sort_order=0),
        Tariff(course_id=course.id, slug="vip", name="VIP", price=29990, is_active=True, sort_order=1),
    ])
    await db_session.commit()
    return course


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    u = User(email="buyer@example.com", telegram_id=31337, registration_source="web",
             accepted_offer=True)
    db_session.add(u)
    await db_session.commit()
    return u


async def _login(client, db, user):
    db.add(UserSession(user_id=user.id, session_token=f"t{user.id}",
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db.commit()
    client.cookies.set("user_session", f"t{user.id}")


async def _window(db, start_delta: timedelta, end_delta: timedelta, enabled=True):
    now = datetime.now(timezone.utc)
    await set_sales_schedule(db, enabled=enabled, start=now + start_delta, end=now + end_delta)


async def _buy(client, slug):
    with patch("app.api.v1.payments._payment_service.create_payment",
               new=AsyncMock(return_value={"payment_id": f"yk_{datetime.now().timestamp()}",
                                           "confirmation_url": "https://pay", "status": "pending"})) as m:
        r = await client.post("/api/v1/payments/create", json={"tariff_slug": slug, "accepted_offer": True})
    return r, m


# --- фазы -----------------------------------------------------------------
def test_phases_and_boundaries():
    s = SalesSchedule(enabled=True, start=NOW, end=NOW + timedelta(days=3))
    assert s.phase(NOW - timedelta(seconds=1)) == PHASE_BEFORE
    assert s.phase(NOW) == PHASE_OPEN
    assert s.phase(NOW + timedelta(days=3) - timedelta(seconds=1)) == PHASE_OPEN
    assert s.phase(NOW + timedelta(days=3)) == PHASE_CLOSED
    assert s.countdown_target(NOW - timedelta(hours=1)) == NOW
    assert s.countdown_target(NOW + timedelta(hours=1)) == NOW + timedelta(days=3)
    assert s.countdown_target(NOW + timedelta(days=4)) is None


def test_disabled_or_incomplete_schedule_is_inactive():
    assert SalesSchedule(enabled=False, start=NOW, end=NOW + timedelta(days=1)).phase(NOW) is None
    assert SalesSchedule(enabled=True, start=NOW, end=None).phase(NOW) is None


@pytest.mark.asyncio
async def test_set_schedule_validation_and_utc_storage(db_session):
    start = datetime(2026, 11, 1, 12, 0, tzinfo=MSK)
    end = datetime(2026, 11, 8, 23, 59, tzinfo=MSK)
    s = await set_sales_schedule(db_session, enabled=True, start=start, end=end)
    assert s.start == datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="позже"):
        await set_sales_schedule(db_session, enabled=True, start=end, end=start)
    with pytest.raises(ValueError, match="Укажите"):
        await set_sales_schedule(db_session, enabled=True, start=None, end=end)


# --- режим лендинга ---------------------------------------------------------
@pytest.mark.asyncio
async def test_effective_mode_follows_schedule(db_session):
    await set_landing_mode(db_session, "sales")
    await _window(db_session, timedelta(hours=1), timedelta(days=2))
    assert await effective_landing_mode(db_session) == "enrollment"      # до старта
    await _window(db_session, timedelta(hours=-1), timedelta(days=2))
    assert await effective_landing_mode(db_session) == "sales"           # идут
    await _window(db_session, timedelta(days=-3), timedelta(hours=-1))
    assert await effective_landing_mode(db_session) == "enrollment"      # закончились
    await _window(db_session, timedelta(days=-3), timedelta(hours=-1), enabled=False)
    assert await effective_landing_mode(db_session) == "sales"           # ручной режим


@pytest.mark.asyncio
async def test_landing_shows_tariffs_only_inside_window(client, db_session, story_course):
    await set_landing_mode(db_session, "enrollment")
    await _window(db_session, timedelta(hours=-1), timedelta(days=2))
    assert "Купить Pro" in (await client.get("/")).text
    await _window(db_session, timedelta(days=-3), timedelta(hours=-1))
    html = (await client.get("/")).text
    assert "Купить Pro" not in html and "Отправить заявку" in html


@pytest.mark.asyncio
async def test_waitlist_accepted_after_sales_closed(client, db_session):
    await set_landing_mode(db_session, "sales")
    await _window(db_session, timedelta(days=-3), timedelta(hours=-1))
    r = await client.post("/waitlist", data={
        "name": "Аня", "phone": "+79990000000", "email": "a@example.com", "accepted_privacy": "on",
    })
    assert r.headers["location"].startswith("/?waitlist=ok")


# --- закрытие новых покупок ----------------------------------------------
@pytest.mark.asyncio
async def test_new_purchase_rejected_before_start(client, db_session, story_course, user):
    await _window(db_session, timedelta(days=1), timedelta(days=5))
    await _login(client, db_session, user)
    r, m = await _buy(client, "pro")
    assert r.status_code == 400
    assert "Старт продаж" in r.json()["detail"]
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_purchase_rejected_after_end(client, db_session, story_course, user):
    await _window(db_session, timedelta(days=-5), timedelta(hours=-1))
    await _login(client, db_session, user)
    r, m = await _buy(client, "self")
    assert r.status_code == 400 and "закрыты" in r.json()["detail"]
    r2 = await client.post("/api/v1/payments/installment", json={"tariff_slug": "pro", "accepted_offer": True})
    assert r2.status_code in (400, 503)


@pytest.mark.asyncio
async def test_upgrade_allowed_outside_window(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _window(db_session, timedelta(days=-5), timedelta(hours=-1))
    await _login(client, db_session, user)
    r, m = await _buy(client, "pro")
    assert r.status_code == 200, r.text
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_purchase_open_inside_window_and_without_schedule(client, db_session, story_course, user):
    await _login(client, db_session, user)
    r, _ = await _buy(client, "self")       # расписания нет — как раньше
    assert r.status_code == 200
    await _window(db_session, timedelta(hours=-1), timedelta(days=2))
    assert await new_sales_closed_reason(db_session) is None


@pytest.mark.asyncio
async def test_paywall_shows_closed_message(client, db_session, story_course, user):
    await _window(db_session, timedelta(days=-5), timedelta(hours=-1))
    await _login(client, db_session, user)
    html = (await client.get("/cabinet/lessons")).text
    assert "Продажи сейчас закрыты" in html
    assert 'id="paywall-pay-btn"' not in html


@pytest.mark.asyncio
async def test_bot_buy_shows_closed_message(db_session, story_course, user):
    from app.bot import bot as bot_module

    await _window(db_session, timedelta(days=1), timedelta(days=5))
    cb = MagicMock()
    cb.data = "buy"
    cb.from_user.id = user.telegram_id
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    await bot_module.process_buy(cb, MagicMock(), db_session)
    assert "Старт продаж" in cb.message.edit_text.await_args.args[0]

    svc = MagicMock()
    svc.create_payment = AsyncMock()
    msg = MagicMock()
    msg.answer = AsyncMock()
    with patch.object(bot_module, "PaymentService", return_value=svc):
        await bot_module.create_payment_and_send(msg, user, story_course, "pro", db_session)
    svc.create_payment.assert_not_awaited()


# --- админка ---------------------------------------------------------------
async def _admin(client, db):
    admin = User(username="boss", email="boss@example.com", role="admin", is_admin=True,
                 registration_source="web")
    db.add(admin)
    await db.flush()
    db.add(AdminSession(user_id=admin.id, session_token="adm",
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db.commit()
    client.cookies.set("admin_session", "adm")


@pytest.mark.asyncio
async def test_admin_saves_schedule_in_msk(client, db_session):
    await _admin(client, db_session)
    r = await client.post("/admin/settings/sales-schedule", data={
        "enabled": "1", "sales_start": "2026-11-01T12:00", "sales_end": "2026-11-08T23:59",
    })
    assert r.status_code == 303 and "schedule_saved=1" in r.headers["location"]
    s = await get_sales_schedule(db_session)
    assert s.enabled
    assert s.start == datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    assert s.end == datetime(2026, 11, 8, 20, 59, tzinfo=timezone.utc)

    html = (await client.get("/admin/settings")).text
    assert "с 1 ноября 2026, 12:00 по 8 ноября 2026, 23:59" in html
    assert 'value="2026-11-01T12:00"' in html


@pytest.mark.asyncio
async def test_admin_schedule_validation_error(client, db_session):
    await _admin(client, db_session)
    r = await client.post("/admin/settings/sales-schedule", data={
        "enabled": "1", "sales_start": "2026-11-08T12:00", "sales_end": "2026-11-01T12:00",
    })
    assert "schedule_error=" in r.headers["location"]
    s = await get_sales_schedule(db_session)
    assert not s.enabled  # ничего не сохранилось


# --- C4: блок отсчёта на лендинге -------------------------------------------
from app.main import _countdown_context


def test_countdown_context_open_phase():
    s = SalesSchedule(enabled=True, start=NOW - timedelta(days=1), end=NOW + timedelta(days=3))
    c = _countdown_context(s, NOW)
    assert c["phase"] == "open"
    assert c["target_iso"] == s.end.isoformat()
    assert c["title"] == "До конца продаж осталось"
    assert c["progress"] == 25.0
    assert "МСК" in c["when_text"]


def test_countdown_context_before_and_none():
    s = SalesSchedule(enabled=True, start=NOW + timedelta(hours=5), end=NOW + timedelta(days=3))
    c = _countdown_context(s, NOW)
    assert c["phase"] == "before" and c["target_iso"] == s.start.isoformat()
    assert c["title"] == "До старта продаж осталось" and c["strip_cta"] == "Записаться"
    closed = SalesSchedule(enabled=True, start=NOW - timedelta(days=3), end=NOW - timedelta(hours=1))
    assert _countdown_context(closed, NOW) is None
    assert _countdown_context(SalesSchedule(), NOW) is None


@pytest.mark.asyncio
async def test_landing_countdown_open(client, db_session, story_course):
    await _window(db_session, timedelta(hours=-1), timedelta(days=2))
    html = (await client.get("/")).text
    assert "До конца продаж осталось" in html
    assert 'class="sales-strip"' in html and "Выбрать тариф →" in html
    assert "data-progress-start=" in html
    assert html.index("До конца продаж осталось") < html.index("Купить Pro")  # над тарифами
    assert "pt-[6.25rem]" in html


@pytest.mark.asyncio
async def test_landing_countdown_before_start(client, db_session, story_course):
    await _window(db_session, timedelta(hours=3), timedelta(days=2))
    html = (await client.get("/")).text
    assert "До старта продаж осталось" in html and "Записаться →" in html
    assert "Отправить заявку" in html and "Купить Pro" not in html
    assert "data-progress-start=" not in html


@pytest.mark.asyncio
async def test_landing_no_countdown_without_schedule_or_after_end(client, db_session, story_course):
    await set_landing_mode(db_session, "sales")
    html = (await client.get("/")).text
    assert "sales-countdown" not in html and 'class="sales-strip"' not in html
    assert "pt-16" in html
    await _window(db_session, timedelta(days=-3), timedelta(hours=-1))
    assert 'class="sales-strip"' not in (await client.get("/")).text


@pytest.mark.asyncio
async def test_countdown_hidden_for_buyer_without_upgrade(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "vip", commit=True)
    await _window(db_session, timedelta(hours=-1), timedelta(days=2))
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert 'class="sales-strip"' not in html and "До конца продаж" not in html


@pytest.mark.asyncio
async def test_countdown_shown_for_self_upgrade(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _window(db_session, timedelta(hours=-1), timedelta(days=2))
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert "Перейдите на Pro или VIP" in html and "До конца продаж осталось" in html
