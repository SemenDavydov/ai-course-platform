"""B8: ежедневная задача — напоминание за 7 дней и снятие has_access после истечения."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.course import Course, Tariff
from app.models.user import User
from app.scripts import access_expiry
from app.services.access import get_access, grant_course_access


@pytest_asyncio.fixture
async def story_course(db_session: AsyncSession) -> Course:
    course = Course(title="AI STORY", description="d", price=14990, is_published=True,
                    slug="ai-story", sort_order=0, is_legacy=False)
    db_session.add(course)
    await db_session.flush()
    db_session.add_all([
        Tariff(course_id=course.id, slug="self", name="Самостоятельный", price=8990,
               is_active=True, sort_order=-1),
        Tariff(course_id=course.id, slug="pro", name="Pro", price=14990, is_active=True),
        Tariff(course_id=course.id, slug="vip", name="VIP", price=29990, is_active=True, sort_order=1),
    ])
    await db_session.commit()
    return course


async def _user_with_self(db, course, email="s@example.com", expires_in=timedelta(days=5), **kw):
    u = User(email=email, registration_source="web", accepted_offer=True, **kw)
    db.add(u)
    await db.commit()
    await grant_course_access(db, u, course.id, "self", commit=True)
    access = await get_access(db, u.id, course.id)
    access.expires_at = datetime.now(timezone.utc) + expires_in
    await db.commit()
    return u


@pytest.mark.asyncio
async def test_reminder_sent_once(db_session, story_course, monkeypatch):
    monkeypatch.setattr(settings, "SITE_URL", "https://davydovaai.ru")
    user = await _user_with_self(db_session, story_course)
    with patch.object(access_expiry, "send_email", return_value=True) as mock_send:
        r1 = await access_expiry.process_access_expiry(db_session)
        r2 = await access_expiry.process_access_expiry(db_session)

    assert r1.reminders_sent == 1 and r2.reminders_sent == 0
    mock_send.assert_called_once()
    to, subject, html = mock_send.call_args.args
    assert to == user.email
    access = await get_access(db_session, user.id, story_course.id)
    until = access.expires_at.strftime("%d.%m.%Y")
    assert until in subject and until in html
    assert "https://davydovaai.ru/#tariffs" in html
    assert access.expiry_reminder_sent_at is not None


@pytest.mark.asyncio
async def test_failed_send_retries_next_run(db_session, story_course):
    await _user_with_self(db_session, story_course)
    with patch.object(access_expiry, "send_email", return_value=False):
        r1 = await access_expiry.process_access_expiry(db_session)
    with patch.object(access_expiry, "send_email", return_value=True):
        r2 = await access_expiry.process_access_expiry(db_session)
    assert (r1.reminders_failed, r1.reminders_sent) == (1, 0)
    assert r2.reminders_sent == 1


@pytest.mark.asyncio
async def test_no_reminder_too_early_or_for_perpetual(db_session, story_course):
    await _user_with_self(db_session, story_course, expires_in=timedelta(days=30))
    pro = User(email="pro@example.com", registration_source="web")
    db_session.add(pro)
    await db_session.commit()
    await grant_course_access(db_session, pro, story_course.id, "pro", commit=True)
    with patch.object(access_expiry, "send_email", return_value=True) as mock_send:
        r = await access_expiry.process_access_expiry(db_session)
    assert r.reminders_sent == 0
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_reminder_when_no_email(db_session, story_course, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ENABLED", True)
    await _user_with_self(db_session, story_course, email=None, telegram_id=555111)
    with patch("app.bot.bot.bot.send_message", new=AsyncMock()) as tg, \
         patch.object(access_expiry, "send_email") as mail:
        r = await access_expiry.process_access_expiry(db_session)
    assert r.reminders_sent == 1
    mail.assert_not_called()
    assert tg.await_args.kwargs["chat_id"] == 555111
    assert "Самостоятельный" in tg.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_expired_access_revokes_has_access(db_session, story_course):
    user = await _user_with_self(db_session, story_course, expires_in=timedelta(days=-1))
    assert user.has_access is True
    with patch.object(access_expiry, "send_email", return_value=True) as mock_send:
        r = await access_expiry.process_access_expiry(db_session)
    await db_session.refresh(user)
    assert r.access_revoked == 1
    assert user.has_access is False
    mock_send.assert_not_called()  # просроченным не напоминаем


# ---------------------------------------------------------------------------
# Апгрейд с лендинга для действующего self
# ---------------------------------------------------------------------------
from app.models.user_session import UserSession
from app.services.site_settings import set_landing_mode


async def _login(client, db, user):
    db.add(UserSession(user_id=user.id, session_token=f"s{user.id}",
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db.commit()
    client.cookies.set("user_session", f"s{user.id}")


@pytest.mark.asyncio
async def test_landing_offers_upgrade_to_active_self(client, db_session, story_course):
    await set_landing_mode(db_session, "sales")
    user = await _user_with_self(db_session, story_course, expires_in=timedelta(days=40))
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert "Доступ открыт" in html and "доступ до" in html
    assert "Перейдите на Pro или VIP" in html
    assert "Купить Pro" in html and "Купить VIP" in html
    assert "Купить Самостоятельный" not in html
    assert "const CAN_UPGRADE = true" in html


@pytest.mark.asyncio
async def test_landing_no_upgrade_section_for_pro(client, db_session, story_course):
    await set_landing_mode(db_session, "sales")
    user = User(email="p@example.com", registration_source="web")
    db_session.add(user)
    await db_session.commit()
    await grant_course_access(db_session, user, story_course.id, "pro", commit=True)
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert "Доступ открыт" in html
    assert 'id="tariffs"' not in html  # как и раньше
    assert "const CAN_UPGRADE = false" in html
