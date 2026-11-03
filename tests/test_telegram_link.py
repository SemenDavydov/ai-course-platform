"""Привязка Telegram к аккаунту сайта: покупатель с сайта должен видеть уроки в боте."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot as bot_module
from app.models.course import Course
from app.models.payment import Payment
from app.models.user import User
from app.models.user_session import UserSession
from app.services.access import grant_course_access
from app.services.auth import create_login_token, link_telegram
from app.templating import templates

TG_ID = 426911587


@pytest_asyncio.fixture
async def course(db_session: AsyncSession) -> Course:
    c = Course(title="AI STORY", description="d", price=1, is_published=True, slug="ai-story",
               sort_order=0, is_legacy=False)
    db_session.add(c)
    await db_session.commit()
    return c


@pytest_asyncio.fixture
async def buyer(db_session: AsyncSession, course: Course) -> User:
    u = User(email="buyer@example.com", registration_source="web", accepted_offer=True)
    u.set_password("password123")
    db_session.add(u)
    await db_session.flush()
    await grant_course_access(db_session, u, course.id, "pro", commit=True)
    return u


async def _login(client, db, user):
    db.add(UserSession(user_id=user.id, session_token=f"s{user.id}",
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db.commit()
    client.cookies.set("user_session", f"s{user.id}")


def _start_message():
    msg = MagicMock()
    msg.from_user.id = TG_ID
    msg.from_user.username = "student"
    msg.from_user.first_name = "Студент"
    msg.from_user.last_name = None
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_merges_empty_bot_record(db_session, buyer):
    """Ровно случай из жалобы: зашла в бота раньше, бот завёл пустую запись."""
    bot_user = User(telegram_id=TG_ID, username="student", has_access=False)
    db_session.add(bot_user)
    await db_session.commit()
    bot_user_id, buyer_id = bot_user.id, buyer.id

    token = await create_login_token(db_session, buyer)
    assert await link_telegram(db_session, token, TG_ID, username="student") == "linked"

    db_session.expire_all()
    rows = (await db_session.execute(select(User).where(User.telegram_id == TG_ID))).scalars().all()
    assert [u.id for u in rows] == [buyer_id]
    assert rows[0].email == "buyer@example.com" and rows[0].username == "student"
    assert await db_session.get(User, bot_user_id) is None


@pytest.mark.asyncio
async def test_bot_record_with_payment_is_never_deleted(db_session, buyer, course):
    bot_user = User(telegram_id=TG_ID, has_access=False)
    db_session.add(bot_user)
    await db_session.flush()
    db_session.add(Payment(user_id=bot_user.id, amount=1, payment_id="pending_bot", status="pending",
                           course_id=course.id, provider="yookassa"))
    await db_session.commit()
    bot_user_id, buyer_id = bot_user.id, buyer.id

    token = await create_login_token(db_session, buyer)
    assert await link_telegram(db_session, token, TG_ID) == "conflict"
    db_session.expire_all()
    assert await db_session.get(User, bot_user_id) is not None
    assert (await db_session.get(User, buyer_id)).telegram_id is None


@pytest.mark.asyncio
async def test_token_is_one_time_and_account_keeps_its_telegram(db_session, buyer):
    buyer_id = buyer.id
    token = await create_login_token(db_session, buyer)
    assert await link_telegram(db_session, token, TG_ID) == "linked"
    assert await link_telegram(db_session, token, 999) == "expired"

    # Новый токен, чужой Telegram: уже привязанный аккаунт не перепривязываем.
    token = await create_login_token(db_session, buyer)
    assert await link_telegram(db_session, token, 999) == "conflict"
    db_session.expire_all()
    assert (await db_session.get(User, buyer_id)).telegram_id == TG_ID


@pytest.mark.asyncio
async def test_start_link_shows_course_not_sales(db_session, buyer):
    token = await create_login_token(db_session, buyer)
    msg = _start_message()
    command = MagicMock(args=f"link_{token}")
    await bot_module.cmd_start(msg, db_session, command)

    assert "привязан" in msg.answer.await_args_list[0].args[0]
    markup = msg.answer.await_args_list[-1].kwargs["reply_markup"]
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "course_story" in buttons and "buy" not in buttons


@pytest.mark.asyncio
async def test_start_with_bad_token_creates_no_user(db_session):
    msg = _start_message()
    await bot_module.cmd_start(msg, db_session, MagicMock(args="link_wrong"))
    assert "устарела" in msg.answer.await_args.args[0]
    found = await db_session.execute(select(User).where(User.telegram_id == TG_ID))
    assert found.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cabinet_redirect_and_button(client, db_session, buyer, monkeypatch):
    monkeypatch.setitem(templates.env.globals, "telegram_bot_url", "https://t.me/TestBot")
    monkeypatch.setitem(templates.env.globals, "telegram_bot_username", "TestBot")
    monkeypatch.setitem(templates.env.globals, "bot_enabled", True)

    anon = await client.get("/cabinet/telegram", follow_redirects=False)
    assert anon.headers["location"] == "/auth/open-lessons"

    await _login(client, db_session, buyer)
    assert 'href="/cabinet/telegram"' in (await client.get("/cabinet/lessons")).text

    r = await client.get("/cabinet/telegram", follow_redirects=False)
    assert r.status_code == 302
    await db_session.refresh(buyer)
    assert r.headers["location"] == f"https://t.me/TestBot?start=link_{buyer.login_token}"
    assert len(f"link_{buyer.login_token}") <= 64  # лимит Telegram на параметр start

    buyer.telegram_id = TG_ID
    await db_session.commit()
    r = await client.get("/cabinet/telegram", follow_redirects=False)
    assert r.headers["location"] == "https://t.me/TestBot"


@pytest.mark.asyncio
async def test_plain_start_tells_site_buyers_how_to_link(db_session, monkeypatch):
    monkeypatch.setattr(bot_module.asyncio, "sleep", AsyncMock())
    msg = _start_message()
    await bot_module.cmd_start(msg, db_session)
    assert "Открыть уроки в боте" in msg.answer.await_args.args[0]
