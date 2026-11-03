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


# --- Покупка в боте с email существующего аккаунта сайта ---------------------
from unittest.mock import patch

from app.config import settings
from app.models.course import Tariff
from app.services.proonline import ProOnlineClient


class FakeState:
    def __init__(self, **data):
        self.data = data
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kw):
        self.data.update(kw)

    async def set_state(self, value):
        self.state = value

    async def clear(self):
        self.data.clear()
        self.state = None


@pytest_asyncio.fixture
async def tariff(db_session, course):
    t = Tariff(course_id=course.id, slug="pro", name="Pro", price=14990, is_active=True)
    db_session.add(t)
    await db_session.commit()
    return t


def _callback(data):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = TG_ID
    cb.message.from_user.id = 777000  # в callback это сам бот, а не покупатель
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _fake_payments():
    svc = MagicMock()
    svc.create_payment = AsyncMock(return_value={"payment_id": "yk_x", "confirmation_url": "https://pay"})
    return svc


@pytest.mark.asyncio
async def test_email_of_site_account_sends_link_not_payment(db_session, buyer, tariff, monkeypatch):
    monkeypatch.setattr(settings, "BOT_USERNAME", "TestBot")
    msg = _start_message()
    msg.text = "  BUYER@example.com "
    state = FakeState(tariff_slug="pro", pay_mode="card")
    svc = _fake_payments()
    with patch.object(bot_module, "PaymentService", return_value=svc), \
         patch.object(bot_module, "enqueue_email") as mail:
        await bot_module.process_email(msg, state, db_session)

    svc.create_payment.assert_not_awaited()
    to, subject, html = mail.call_args.args
    assert to == "buyer@example.com"
    assert "если это были не вы" in html.lower()
    assert state.data == {}

    bot_user = (await db_session.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one()
    assert bot_user.email is None  # чужой email записи бота не присваиваем
    assert bot_user.has_access is not True

    # Кнопка из письма объединяет аккаунты: покупка и Telegram в одной записи.
    token = html.split("start=link_")[1].split('"')[0]
    buyer_id = buyer.id
    assert await link_telegram(db_session, token, TG_ID) == "linked"
    db_session.expire_all()
    merged = (await db_session.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one()
    assert merged.id == buyer_id


@pytest.mark.asyncio
async def test_new_email_continues_to_payment(db_session, course, tariff):
    msg = _start_message()
    msg.text = "New@Example.com"
    svc = _fake_payments()
    with patch.object(bot_module, "PaymentService", return_value=svc), \
         patch.object(bot_module, "enqueue_email") as mail:
        await bot_module.process_email(msg, FakeState(tariff_slug="pro", pay_mode="card"), db_session)
    mail.assert_not_called()
    svc.create_payment.assert_awaited_once()
    user = (await db_session.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one()
    assert user.email == "new@example.com"


@pytest.mark.asyncio
async def test_installment_asks_email_then_opens_form(db_session, course, tariff):
    cb = _callback("pay_installment")
    state = FakeState(tariff_slug="pro")
    fake = AsyncMock(return_value={"questionnaire_url": "https://po/q", "session_status": "new"})
    with patch.object(ProOnlineClient, "configured", new=property(lambda self: True)), \
         patch.object(ProOnlineClient, "create_payment_form_session", new=fake):
        await bot_module.process_pay_installment(cb, state, db_session)
        assert state.state == bot_module.Form.waiting_for_email
        fake.assert_not_awaited()

        msg = _start_message()
        msg.text = "lead@example.com"
        await bot_module.process_email(msg, state, db_session)
    fake.assert_awaited_once()
    assert "Перейти к анкете" in str(msg.answer.await_args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_existing_bot_email_also_checked_for_both_pay_modes(db_session, buyer, tariff):
    db_session.add(User(telegram_id=TG_ID, email="BUYER@example.com", registration_source="bot_migrated"))
    await db_session.commit()
    svc = _fake_payments()
    fake = AsyncMock()
    with patch.object(bot_module, "PaymentService", return_value=svc), \
         patch.object(bot_module, "enqueue_email") as mail, \
         patch.object(ProOnlineClient, "create_payment_form_session", new=fake):
        await bot_module.process_pay_card(_callback("pay_card"), FakeState(tariff_slug="pro"), db_session)
        await bot_module.process_pay_installment(_callback("pay_installment"), FakeState(tariff_slug="pro"), db_session)
    svc.create_payment.assert_not_awaited()
    fake.assert_not_awaited()
    assert mail.call_count == 2


@pytest.mark.asyncio
async def test_account_with_other_telegram_gets_no_email(db_session, buyer, tariff):
    buyer.telegram_id = 111
    await db_session.commit()
    msg = _start_message()
    msg.text = "buyer@example.com"
    with patch.object(bot_module, "enqueue_email") as mail:
        await bot_module.process_email(msg, FakeState(tariff_slug="pro"), db_session)
    mail.assert_not_called()
    assert "другому Telegram" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_link_emails_are_rate_limited(db_session, buyer, tariff):
    with patch.object(bot_module, "enqueue_email") as mail:
        for _ in range(5):
            msg = _start_message()
            msg.text = "buyer@example.com"
            await bot_module.process_email(msg, FakeState(tariff_slug="pro"), db_session)
    assert mail.call_count == 3
    assert "уже отправлено" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_link_token_lives_one_hour_not_fifteen_minutes(db_session, buyer):
    token = await create_login_token(db_session, buyer)
    buyer.login_token_sent_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    await db_session.commit()
    assert await link_telegram(db_session, token, TG_ID) == "linked"

    other = User(email="late@example.com", registration_source="web")
    db_session.add(other)
    await db_session.commit()
    token = await create_login_token(db_session, other)
    other.login_token_sent_at = datetime.now(timezone.utc) - timedelta(minutes=61)
    await db_session.commit()
    assert await link_telegram(db_session, token, 222) == "expired"


@pytest.mark.asyncio
async def test_link_limit_is_per_user_not_shared_through_the_bot(db_session, tariff):
    """В callback message.from_user — бот: общий лимит заблокировал бы всех покупателей."""
    for i in range(7):
        db_session.add(User(email=f"site{i}@example.com", registration_source="web"))
        db_session.add(User(telegram_id=1000 + i, email=f"site{i}@example.com", registration_source="bot_migrated"))
    await db_session.commit()
    with patch.object(bot_module, "enqueue_email") as mail:
        for i in range(7):
            cb = _callback("pay_card")
            cb.from_user.id = 1000 + i
            await bot_module.process_pay_card(cb, FakeState(tariff_slug="pro"), db_session)
    assert mail.call_count == 7
