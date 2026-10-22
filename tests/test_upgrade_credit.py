"""C2: зачёт уже оплаченного при переходе на тариф выше."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course, Tariff
from app.models.payment import Payment
from app.models.user import User
from app.models.user_session import UserSession
from app.services.access import get_access, grant_course_access, paid_credit, upgrade_price


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
    u = User(email="up@example.com", telegram_id=987654, registration_source="web",
             accepted_offer=True)
    db_session.add(u)
    await db_session.commit()
    return u


async def _paid(db, user, course, slug, amount, status="succeeded"):
    db.add(Payment(user_id=user.id, amount=amount, payment_id=f"p_{slug}_{amount}_{status}",
                   status=status, course_id=course.id, tariff_slug=slug, provider="yookassa"))
    await db.commit()
    await grant_course_access(db, user, course.id, slug, commit=True)


async def _login(client, db, user):
    db.add(UserSession(user_id=user.id, session_token=f"t{user.id}",
                       expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db.commit()
    client.cookies.set("user_session", f"t{user.id}")


async def _buy(client, slug):
    with patch("app.api.v1.payments._payment_service.create_payment",
               new=AsyncMock(return_value={"payment_id": f"yk_{slug}_{datetime.now().timestamp()}",
                                           "confirmation_url": "https://pay", "status": "pending"})) as m:
        r = await client.post("/api/v1/payments/create",
                              json={"tariff_slug": slug, "accepted_offer": True})
    return r, m


def test_upgrade_price_never_negative():
    assert upgrade_price(14990, 8990) == 6000
    assert upgrade_price(8990, 14990) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("history, target, expected", [
    ([("self", 8990)], "pro", 6000),
    ([("self", 8990)], "vip", 21000),
    ([("pro", 14990)], "vip", 15000),
    ([("self", 8990), ("pro", 6000)], "vip", 15000),
])
async def test_api_charges_difference(client, db_session, story_course, user, history, target, expected):
    for slug, amount in history:
        await _paid(db_session, user, story_course, slug, amount)
    await _login(client, db_session, user)
    r, m = await _buy(client, target)
    assert r.status_code == 200, r.text
    kw = m.await_args.kwargs
    assert kw["amount"] == expected
    assert "зачтено" in kw["description"]
    p = (await db_session.execute(
        select(Payment).where(Payment.status == "pending", Payment.user_id == user.id)
    )).scalar_one()
    assert p.amount == expected and p.tariff_slug == target


@pytest.mark.asyncio
async def test_no_credit_after_self_expired(client, db_session, story_course, user):
    await _paid(db_session, user, story_course, "self", 8990)
    access = await get_access(db_session, user.id, story_course.id)
    access.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()
    await _login(client, db_session, user)
    r, m = await _buy(client, "pro")
    assert r.status_code == 200
    assert m.await_args.kwargs["amount"] == 14990


@pytest.mark.asyncio
async def test_admin_granted_access_gives_no_credit(db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    existing = await get_access(db_session, user.id, story_course.id)
    assert await paid_credit(db_session, user.id, story_course.id, existing) == 0


@pytest.mark.asyncio
async def test_refunded_and_pending_not_counted(db_session, story_course, user):
    await _paid(db_session, user, story_course, "self", 8990)
    db_session.add_all([
        Payment(user_id=user.id, amount=5000, payment_id="refunded1", status="refunded",
                course_id=story_course.id, provider="yookassa"),
        Payment(user_id=user.id, amount=7000, payment_id="pending1", status="pending",
                course_id=story_course.id, provider="yookassa"),
    ])
    await db_session.commit()
    existing = await get_access(db_session, user.id, story_course.id)
    assert await paid_credit(db_session, user.id, story_course.id, existing) == 8990


@pytest.mark.asyncio
async def test_installment_uses_difference(client, db_session, story_course, user):
    from app.services.proonline import ProOnlineClient

    await _paid(db_session, user, story_course, "self", 8990)
    await _login(client, db_session, user)
    fake = AsyncMock(return_value={"questionnaire_url": "https://po/q", "session_status": "new"})
    with patch.object(ProOnlineClient, "configured", new=property(lambda self: True)), \
         patch.object(ProOnlineClient, "create_payment_form_session", new=fake):
        r = await client.post("/api/v1/payments/installment",
                              json={"tariff_slug": "pro", "accepted_offer": True})
    assert r.status_code == 200, r.text
    assert fake.await_args.kwargs["amount"] == 6000


@pytest.mark.asyncio
async def test_webhook_accepts_discounted_amount(client, db_session, story_course, user, yookassa_api):
    await _paid(db_session, user, story_course, "self", 8990)
    await _login(client, db_session, user)
    await _buy(client, "pro")
    p = (await db_session.execute(
        select(Payment).where(Payment.status == "pending", Payment.user_id == user.id)
    )).scalar_one()
    yookassa_api.set_payment(amount="6000.00")
    with patch("app.tasks.send_email"), patch("app.api.webhooks.bot.send_message", new=AsyncMock()):
        r = await client.post("/webhooks/yookassa", json={
            "event": "payment.succeeded",
            "object": {"id": p.payment_id, "status": "succeeded",
                       "amount": {"value": "6000.00", "currency": "RUB"}},
        })
    assert r.status_code == 200, r.text
    access = await get_access(db_session, user.id, story_course.id)
    await db_session.refresh(access)
    assert access.tariff_slug == "pro" and access.expires_at is None


@pytest.mark.asyncio
async def test_bot_shows_and_charges_difference(db_session, story_course, user):
    from app.bot import bot as bot_module

    await _paid(db_session, user, story_course, "self", 8990)

    cb = MagicMock()
    cb.data = "buy"
    cb.from_user.id = user.telegram_id
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    state = MagicMock()
    await bot_module.process_buy(cb, state, db_session)
    markup = cb.message.edit_text.await_args.kwargs.get("reply_markup") or cb.message.edit_text.await_args.args[1]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "Pro — 6000₽ (с учётом оплаченного)" in labels

    svc = MagicMock()
    svc.create_payment = AsyncMock(return_value={"payment_id": "yk_bot_up", "confirmation_url": "https://pay"})
    msg = MagicMock()
    msg.answer = AsyncMock()
    with patch.object(bot_module, "PaymentService", return_value=svc):
        await bot_module.create_payment_and_send(msg, user, story_course, "pro", db_session)
    assert svc.create_payment.await_args.kwargs["amount"] == 6000
    assert "6000₽ (с учётом оплаченных 8990₽)" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_landing_upgrade_shows_discounted_price(client, db_session, story_course, user):
    from app.services.site_settings import set_landing_mode

    await set_landing_mode(db_session, "sales")
    await _paid(db_session, user, story_course, "self", 8990)
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert "Перейдите на Pro или VIP" in html
    assert "6 000" in html and "21 000" in html
    assert "с учётом оплаченных 8 990" in html
    assert "price: 6000" in html  # модалка оплаты получает цену с зачётом
