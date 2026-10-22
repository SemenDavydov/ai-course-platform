"""Telegram-бот: тариф «Самостоятельный» и общие правила покупки (без реального Telegram)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot as bot_module
from app.models.course import Course, Module, Lesson, Tariff
from app.models.user import User
from app.services.access import get_access, grant_course_access

TG_ID = 424242


class FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kw):
        self.data.update(kw)

    async def set_state(self, s):
        self.state = s

    async def clear(self):
        self.data.clear()
        self.state = None


def _callback(data: str):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = TG_ID
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.answer = AsyncMock()
    cb.bot.send_message = AsyncMock()
    return cb


def _shown_text(cb) -> str:
    calls = cb.message.edit_text.await_args_list + cb.message.answer.await_args_list
    return "\n".join(str(c.args[0]) for c in calls if c.args)


def _buttons(cb) -> list[str]:
    calls = cb.message.edit_text.await_args_list + cb.message.answer.await_args_list
    out = []
    for c in calls:
        markup = c.kwargs.get("reply_markup") or (c.args[1] if len(c.args) > 1 else None)
        if markup:
            out += [b.callback_data or b.url for row in markup.inline_keyboard for b in row]
    return out


@pytest_asyncio.fixture
async def story_course(db_session: AsyncSession) -> Course:
    course = Course(title="AI STORY", description="Курс", price=14990, is_published=True,
                    slug="ai-story", sort_order=0, is_legacy=False)
    db_session.add(course)
    await db_session.flush()
    db_session.add_all([
        Tariff(course_id=course.id, slug="self", name="Самостоятельный", price=8990,
               is_active=True, sort_order=-1),
        Tariff(course_id=course.id, slug="pro", name="Pro", price=14990, is_active=True, sort_order=0),
        Tariff(course_id=course.id, slug="vip", name="VIP", price=29990, is_active=True, sort_order=1),
    ])
    mod = Module(course_id=course.id, title="М1", order=1)
    db_session.add(mod)
    await db_session.flush()
    db_session.add(Lesson(course_id=course.id, module_id=mod.id, title="У1", order=101, video_id="pending"))
    await db_session.commit()
    return course


@pytest_asyncio.fixture
async def tg_user(db_session: AsyncSession) -> User:
    u = User(telegram_id=TG_ID, email="tg@example.com", accepted_offer=True,
             registration_source="bot_migrated")
    db_session.add(u)
    await db_session.commit()
    return u


def _fake_payment_service():
    svc = MagicMock()
    svc.create_payment = AsyncMock(return_value={
        "payment_id": f"yk_bot_{datetime.now().timestamp()}",
        "confirmation_url": "https://yookassa.test/pay",
        "status": "pending",
    })
    return svc


@pytest.mark.asyncio
async def test_tariff_self_goes_straight_to_card(db_session, story_course, tg_user):
    svc = _fake_payment_service()
    cb = _callback("tariff_self")
    state = FakeState()
    with patch.object(bot_module, "PaymentService", return_value=svc):
        await bot_module.process_tariff(cb, state, db_session)

    svc.create_payment.assert_awaited_once()
    kw = svc.create_payment.await_args.kwargs
    assert kw["tariff_slug"] == "self" and kw["amount"] == 8990
    text = _shown_text(cb)
    assert "Как хотите оплатить" not in text
    assert "Самостоятельный" in text
    assert "https://yookassa.test/pay" in _buttons(cb)


@pytest.mark.asyncio
async def test_tariff_pro_still_offers_installment(db_session, story_course, tg_user):
    cb = _callback("tariff_pro")
    await bot_module.process_tariff(cb, FakeState(), db_session)
    assert "Как хотите оплатить" in _shown_text(cb)
    assert "pay_installment" in _buttons(cb)


@pytest.mark.asyncio
async def test_installment_for_self_rejected(db_session, story_course, tg_user):
    cb = _callback("pay_installment")
    with patch.object(bot_module, "ProOnlineClient") as po:
        await bot_module.process_pay_installment(cb, FakeState({"tariff_slug": "self"}), db_session)
    po.assert_not_called()
    assert "только картой" in _shown_text(cb)
    assert "pay_card" in _buttons(cb)


@pytest.mark.asyncio
async def test_bot_enforces_purchase_rules(db_session, story_course, tg_user):
    """Pro-покупатель не может купить self через бота (раньше бот правил не проверял)."""
    await grant_course_access(db_session, tg_user, story_course.id, "pro", commit=True)
    svc = _fake_payment_service()
    msg = MagicMock()
    msg.answer = AsyncMock()
    with patch.object(bot_module, "PaymentService", return_value=svc):
        await bot_module.create_payment_and_send(msg, tg_user, story_course, "self", db_session)
    svc.create_payment.assert_not_awaited()
    assert "Pro-доступ" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_buy_after_expired_self_offers_only_pro_vip(db_session, story_course, tg_user):
    await grant_course_access(db_session, tg_user, story_course.id, "self", commit=True)
    access = await get_access(db_session, tg_user.id, story_course.id)
    access.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    cb = _callback("buy")
    await bot_module.process_buy(cb, FakeState(), db_session)
    text = _shown_text(cb)
    assert "закончился" in text
    assert "уже куплен" not in text
    btns = _buttons(cb)
    assert "tariff_pro" in btns and "tariff_vip" in btns
    assert "tariff_self" not in btns


@pytest.mark.asyncio
async def test_about_active_self_shows_expiry_and_upgrade(db_session, story_course, tg_user):
    access = await grant_course_access(db_session, tg_user, story_course.id, "self", commit=True)
    cb = _callback("about")
    await bot_module.process_about(cb, db_session)
    text = _shown_text(cb)
    assert "Самостоятельный" in text
    assert access.expires_at.strftime("%d.%m.%Y") in text
    btns = _buttons(cb)
    assert "course_story" in btns and "buy" in btns


@pytest.mark.asyncio
async def test_buy_with_vip_says_already_bought(db_session, story_course, tg_user):
    await grant_course_access(db_session, tg_user, story_course.id, "vip", commit=True)
    cb = _callback("buy")
    await bot_module.process_buy(cb, FakeState(), db_session)
    assert "уже куплен" in _shown_text(cb)
    assert not any(b and b.startswith("tariff_") for b in _buttons(cb))
