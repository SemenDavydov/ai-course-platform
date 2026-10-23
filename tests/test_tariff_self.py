"""Тариф «Самостоятельный» (self): срок доступа, апгрейды, истечение."""
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course, Lesson, Module, Tariff
from app.models.user import User
from app.services.access import (
    access_is_active,
    get_access,
    grant_course_access,
    list_accessible_courses,
    list_user_accesses,
    refresh_user_has_access,
    user_has_course,
)
from app.services.tariffs import SELF_ACCESS_DAYS, tariff_label


@pytest_asyncio.fixture
async def story_course(db_session: AsyncSession) -> Course:
    course = Course(
        title="AI STORY", description="new", price=14990, is_published=True,
        slug="ai-story", sort_order=0, is_legacy=False,
    )
    db_session.add(course)
    await db_session.flush()
    db_session.add_all([
        Tariff(course_id=course.id, slug="self", name="Самостоятельный", price=8990,
               is_active=True, sort_order=-1),
        Tariff(course_id=course.id, slug="pro", name="Pro", price=14990, is_active=True),
        Tariff(course_id=course.id, slug="vip", name="VIP", price=29990, is_active=True,
               sort_order=1),
    ])
    mod = Module(course_id=course.id, title="Введение", order=1, button_label="Модуль 1")
    db_session.add(mod)
    await db_session.flush()
    db_session.add(Lesson(course_id=course.id, module_id=mod.id, title="Урок 1",
                          order=101, video_id="pending"))
    await db_session.commit()
    await db_session.refresh(course)
    return course


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    u = User(email="self@example.com", registration_source="web", accepted_offer=True)
    u.set_password("password123")
    db_session.add(u)
    await db_session.commit()
    return u


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _expire(db: AsyncSession, user: User, course: Course) -> None:
    access = await get_access(db, user.id, course.id)
    access.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.commit()


@pytest.mark.asyncio
async def test_self_grant_sets_60_day_expiry(db_session, story_course, user):
    before = datetime.now(timezone.utc)
    access = await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    assert access.tariff_slug == "self"
    expected = before + timedelta(days=SELF_ACCESS_DAYS)
    assert abs((_utc(access.expires_at) - expected).total_seconds()) < 60
    assert await user_has_course(db_session, user, story_course.id)
    assert user.has_access is True


@pytest.mark.asyncio
async def test_pro_and_vip_are_perpetual(db_session, story_course, user):
    access = await grant_course_access(db_session, user, story_course.id, "pro", commit=True)
    assert access.expires_at is None
    access = await grant_course_access(db_session, user, story_course.id, "vip", commit=True)
    assert access.tariff_slug == "vip" and access.expires_at is None


@pytest.mark.asyncio
async def test_expired_self_hides_course(db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)

    assert not await user_has_course(db_session, user, story_course.id)
    assert await list_accessible_courses(db_session, user.id) == []
    assert await list_user_accesses(db_session, user.id) == []
    expired = await list_user_accesses(db_session, user.id, include_expired=True)
    assert [a.tariff_slug for a in expired] == ["self"]


@pytest.mark.asyncio
async def test_upgrade_self_to_pro_removes_expiry(db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    access = await grant_course_access(db_session, user, story_course.id, "pro", commit=True)
    assert access.tariff_slug == "pro"
    assert access.expires_at is None
    assert access.expiry_reminder_sent_at is None


@pytest.mark.asyncio
async def test_pro_after_expired_self_restores_access(db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    access = await grant_course_access(db_session, user, story_course.id, "pro", commit=True)
    assert access.tariff_slug == "pro" and access.expires_at is None
    assert await user_has_course(db_session, user, story_course.id)


@pytest.mark.asyncio
async def test_self_never_downgrades_active_perpetual(db_session, story_course, user):
    for perpetual in ("pro", "vip", "legacy"):
        access = await get_access(db_session, user.id, story_course.id)
        if access:
            await db_session.delete(access)
            await db_session.commit()
        await grant_course_access(db_session, user, story_course.id, perpetual, commit=True)
        access = await grant_course_access(db_session, user, story_course.id, "self", commit=True)
        assert access.tariff_slug == perpetual
        assert access.expires_at is None


@pytest.mark.asyncio
async def test_refresh_has_access_after_expiry(db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    assert await refresh_user_has_access(db_session, user) is False
    assert user.has_access is False


@pytest.mark.asyncio
async def test_refresh_keeps_flag_for_users_without_access_rows(db_session, user):
    # Старые бот-покупатели: has_access=True без записей user_course_access.
    user.has_access = True
    await db_session.commit()
    assert await refresh_user_has_access(db_session, user) is True


def test_access_is_active_handles_naive_and_none():
    class A:
        expires_at = None
    assert access_is_active(None) is False
    assert access_is_active(A()) is True
    A.expires_at = datetime.utcnow() + timedelta(days=1)  # naive = UTC
    assert access_is_active(A()) is True
    A.expires_at = datetime.utcnow() - timedelta(days=1)
    assert access_is_active(A()) is False


def test_tariff_labels():
    assert tariff_label("self") == "Самостоятельный"
    assert tariff_label("PRO") == "Pro"
    assert tariff_label("vip") == "VIP"
    assert tariff_label(None) == ""


# ---------------------------------------------------------------------------
# B3: правила покупки через API
# ---------------------------------------------------------------------------
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models.payment import Payment
from app.models.user_session import UserSession


async def _login(client, db_session, user) -> None:
    token = f"sess_{user.id}"
    db_session.add(UserSession(
        user_id=user.id, session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    await db_session.commit()
    client.cookies.set("user_session", token)


FAKE_PAYMENT = {"payment_id": "yk_1", "confirmation_url": "https://yookassa.test/pay", "status": "pending"}


async def _buy(client, slug: str):
    with patch(
        "app.api.v1.payments._payment_service.create_payment",
        new=AsyncMock(return_value=dict(FAKE_PAYMENT, payment_id=f"yk_{slug}_{datetime.now().timestamp()}")),
    ) as m:
        r = await client.post(
            "/api/v1/payments/create", json={"tariff_slug": slug, "accepted_offer": True}
        )
    return r, m


@pytest.mark.asyncio
async def test_buy_self_creates_yookassa_payment(client, db_session, story_course, user):
    await _login(client, db_session, user)
    r, m = await _buy(client, "self")
    assert r.status_code == 200, r.text
    assert r.json()["confirmation_url"] == "https://yookassa.test/pay"
    assert m.await_args.kwargs["amount"] == 8990
    assert m.await_args.kwargs["tariff_slug"] == "self"
    p = (await db_session.execute(select(Payment).where(Payment.user_id == user.id))).scalar_one()
    assert (p.tariff_slug, p.amount, p.provider, p.status) == ("self", 8990, "yookassa", "pending")


@pytest.mark.asyncio
async def test_self_installment_rejected_without_calling_proonline(client, db_session, story_course, user):
    from app.services.proonline import ProOnlineClient

    await _login(client, db_session, user)
    with patch.object(ProOnlineClient, "create_payment_form_session", new=AsyncMock()) as po:
        r = await client.post(
            "/api/v1/payments/installment", json={"tariff_slug": "self", "accepted_offer": True}
        )
    assert r.status_code == 400
    assert "только картой" in r.json()["detail"]
    po.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current, expired, wanted, allowed",
    [
        ("self", False, "self", False),
        ("self", False, "pro", True),
        ("self", False, "vip", True),
        ("self", True, "self", False),
        ("self", True, "pro", True),
        ("self", True, "vip", True),
        ("pro", False, "self", False),
        ("pro", False, "pro", False),
        ("pro", False, "vip", True),
        ("vip", False, "self", False),
        ("vip", False, "pro", False),
        ("legacy", False, "self", False),
        ("legacy", False, "pro", True),
    ],
)
async def test_purchase_rules_matrix(
    client, db_session, story_course, user, current, expired, wanted, allowed
):
    await grant_course_access(db_session, user, story_course.id, current, commit=True)
    if expired:
        await _expire(db_session, user, story_course)
    await _login(client, db_session, user)
    r, m = await _buy(client, wanted)
    assert (r.status_code == 200) is allowed, r.text
    assert m.await_count == (1 if allowed else 0)


@pytest.mark.asyncio
async def test_active_self_message_shows_expiry_date(client, db_session, story_course, user):
    access = await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _login(client, db_session, user)
    r, _ = await _buy(client, "self")
    assert access.expires_at.strftime("%d.%m.%Y") in r.json()["detail"]


@pytest.mark.asyncio
async def test_expired_self_message_offers_upgrade(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    await _login(client, db_session, user)
    r, _ = await _buy(client, "self")
    assert "Pro и VIP" in r.json()["detail"]


# ---------------------------------------------------------------------------
# B4: уведомления после оплаты self
# ---------------------------------------------------------------------------
from app.config import settings


async def _pending_self_payment(db_session, story_course, user, payment_id: str) -> Payment:
    tariff = (await db_session.execute(
        select(Tariff).where(Tariff.course_id == story_course.id, Tariff.slug == "self")
    )).scalar_one()
    p = Payment(
        user_id=user.id, amount=8990.0, payment_id=payment_id, status="pending",
        course_id=story_course.id, tariff_id=tariff.id, tariff_slug="self", provider="yookassa",
    )
    db_session.add(p)
    await db_session.commit()
    return p


def _yk_body(payment_id: str) -> dict:
    return {"event": "payment.succeeded", "object": {"id": payment_id, "status": "succeeded",
            "amount": {"value": "8990.00", "currency": "RUB"}}}


@pytest.mark.asyncio
async def test_webhook_self_email_has_expiry_and_self_chat(
    client, db_session, story_course, user, yookassa_api, monkeypatch
):
    monkeypatch.setattr(settings, "PRO_CHAT_INVITE_URL", "https://t.me/+PRO_SECRET")
    monkeypatch.setattr(settings, "VIP_CHAT_INVITE_URL", "https://t.me/+VIP_SECRET")
    monkeypatch.setattr(settings, "SELF_CHAT_INVITE_URL", "https://t.me/+T5nhnyDyzhhmZjUy")
    yookassa_api.set_payment(amount="8990.00")
    await _pending_self_payment(db_session, story_course, user, "yk_self_1")

    with patch("app.tasks.send_email") as mock_email, patch(
        "app.api.webhooks.bot.send_message", new=AsyncMock()
    ) as mock_tg:
        r = await client.post("/webhooks/yookassa", json=_yk_body("yk_self_1"))
    assert r.status_code == 200, r.text

    access = await get_access(db_session, user.id, story_course.id)
    await db_session.refresh(access)
    assert access.tariff_slug == "self" and access.expires_at is not None
    until = access.expires_at.strftime("%d.%m.%Y")

    mock_tg.assert_not_called()
    mock_email.assert_called_once()
    to, subject, html = mock_email.call_args.args[:3]
    assert to == user.email
    assert "Самостоятельный" in html
    assert until in html
    assert "https://t.me/+T5nhnyDyzhhmZjUy" in html
    assert "Войти в чат учеников" in html
    assert "ai_story_news" not in html
    assert "PRO_SECRET" not in html and "VIP_SECRET" not in html


@pytest.mark.asyncio
async def test_webhook_self_telegram_text(
    client, db_session, story_course, yookassa_api, monkeypatch
):
    monkeypatch.setattr(settings, "BOT_ENABLED", True)
    monkeypatch.setattr(settings, "PRO_CHAT_INVITE_URL", "https://t.me/+PRO_SECRET")
    monkeypatch.setattr(settings, "SELF_CHAT_INVITE_URL", "https://t.me/+T5nhnyDyzhhmZjUy")
    yookassa_api.set_payment(amount="8990.00")
    tg_user = User(telegram_id=777000, email="tg@example.com", registration_source="bot_migrated")
    db_session.add(tg_user)
    await db_session.commit()
    await _pending_self_payment(db_session, story_course, tg_user, "yk_self_tg")

    with patch("app.tasks.send_email"), patch(
        "app.api.webhooks.bot.send_message", new=AsyncMock()
    ) as mock_tg:
        r = await client.post("/webhooks/yookassa", json=_yk_body("yk_self_tg"))
    assert r.status_code == 200
    text = mock_tg.call_args.kwargs["text"]
    assert "(Самостоятельный)" in text and "на 2 месяца — до" in text
    assert "https://t.me/+T5nhnyDyzhhmZjUy" in text and "PRO_SECRET" not in text
    buttons = [b.text for row in mock_tg.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert "💬 Чат учеников" in buttons


# ---------------------------------------------------------------------------
# B5: сайт — лендинг, paywall, кабинет
# ---------------------------------------------------------------------------
async def _sales_mode(db_session):
    from app.services.site_settings import set_landing_mode
    await set_landing_mode(db_session, "sales")


@pytest.mark.asyncio
async def test_landing_shows_three_tariffs(client, db_session, story_course):
    await _sales_mode(db_session)
    r = await client.get("/")
    assert r.status_code == 200
    html = r.text
    for name in ("Купить Самостоятельный", "Купить Pro", "Купить VIP"):
        assert name in html
    assert "lg:grid-cols-3" in html
    assert "Рассрочка на тарифы Pro и VIP" in html
    assert 'const INSTALLMENT_SLUGS = ["pro", "vip"]' in html
    # self идёт первым (sort_order=-1)
    assert html.index("Купить Самостоятельный") < html.index("Купить Pro") < html.index("Купить VIP")


@pytest.mark.asyncio
async def test_landing_hides_self_after_expiry(client, db_session, story_course, user):
    await _sales_mode(db_session)
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert "Купить Самостоятельный" not in html
    assert "Купить Pro" in html and "Купить VIP" in html
    assert "Срок тарифа «Самостоятельный» закончился" in html


@pytest.mark.asyncio
async def test_landing_purchased_block_shows_label(client, db_session, story_course, user, monkeypatch):
    monkeypatch.setattr(settings, "SELF_CHAT_INVITE_URL", "https://t.me/+T5nhnyDyzhhmZjUy")
    await _sales_mode(db_session)
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _login(client, db_session, user)
    html = (await client.get("/")).text
    assert "Доступ открыт" in html
    assert ">Самостоятельный</span>" in html
    assert "Войти в чат учеников" in html
    assert 'href="https://t.me/+T5nhnyDyzhhmZjUy"' in html


@pytest.mark.asyncio
async def test_paywall_for_expired_self(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    await _login(client, db_session, user)
    html = (await client.get("/cabinet/lessons")).text
    assert "Вы ещё не оплатили курс" in html  # paywall
    assert 'data-slug="self"' not in html
    assert 'data-slug="pro"' in html and 'data-slug="vip"' in html
    assert "Срок тарифа «Самостоятельный» закончился" in html


@pytest.mark.asyncio
async def test_paywall_new_user_three_tariffs(client, db_session, story_course, user):
    await _login(client, db_session, user)
    html = (await client.get("/cabinet/lessons")).text
    for slug in ("self", "pro", "vip"):
        assert f'data-slug="{slug}"' in html
    assert "lg:grid-cols-3" in html
    assert 'PAYWALL_INSTALLMENT_SLUGS = ["pro", "vip"]' in html


@pytest.mark.asyncio
async def test_cabinet_shows_tariff_label(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _login(client, db_session, user)
    html = (await client.get("/cabinet/lessons")).text
    assert "Самостоятельный" in html
    profile = (await client.get("/cabinet/profile")).text
    assert "Самостоятельный" in profile


# ---------------------------------------------------------------------------
# B7: админка
# ---------------------------------------------------------------------------
from app.models.admin_session import AdminSession


async def _admin_login(client, db_session) -> User:
    admin = User(username="boss", email="boss@example.com", role="admin", is_admin=True,
                 registration_source="web")
    db_session.add(admin)
    await db_session.flush()
    db_session.add(AdminSession(
        user_id=admin.id, session_token="admin_tok",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    await db_session.commit()
    client.cookies.set("admin_session", "admin_tok")
    return admin


@pytest.mark.asyncio
async def test_admin_grants_self_with_expiry(client, db_session, story_course, user):
    await _admin_login(client, db_session)
    r = await client.post(
        f"/admin/users/{user.id}/grant-course",
        data={"course_id": story_course.id, "tariff_slug": "self"},
    )
    assert r.status_code == 303
    access = await get_access(db_session, user.id, story_course.id)
    await db_session.refresh(access)
    assert access.tariff_slug == "self" and access.expires_at is not None

    html = (await client.get(f"/admin/users/{user.id}")).text
    assert "Действует до" in html
    assert access.expires_at.strftime("%d.%m.%Y") in html
    assert "Самостоятельный" in html


@pytest.mark.asyncio
async def test_admin_expired_course_can_be_granted_again(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    await _admin_login(client, db_session)
    html = (await client.get(f"/admin/users/{user.id}")).text
    assert "истёк" in html
    # Курс снова есть в списке «Выдать доступ»
    grant_form = html.split('action="/admin/users/%d/grant-course"' % user.id)[1]
    assert f'<option value="{story_course.id}">' in grant_form
    assert 'value="self"' in grant_form


@pytest.mark.asyncio
async def test_admin_toggle_on_expired_grants_instead_of_revoking(client, db_session, story_course, user):
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _expire(db_session, user, story_course)
    await _admin_login(client, db_session)
    r = await client.post(f"/admin/users/{user.id}/toggle-access")
    assert r.status_code == 200 and r.json()["has_access"] is True
    access = await get_access(db_session, user.id, story_course.id)
    await db_session.refresh(access)
    assert access is not None and access.tariff_slug == "pro" and access.expires_at is None


@pytest.mark.asyncio
async def test_offer_describes_self_tariff(client):
    html = (await client.get("/offer")).text
    assert "AI STORY: воплоти свою историю" in html
    assert "2 (два) месяца (60 календарных дней) с момента оплаты" in html
    assert "рассрочка для него не предоставляется" in html
    assert "доплатив разницу" in html
    assert "9 990 (девять" not in html  # устаревшие цены убраны


def test_self_chat_falls_back_to_public_channel(monkeypatch):
    from app.templating import chat_invite_url_for
    monkeypatch.setattr(settings, "SELF_CHAT_INVITE_URL", "")
    monkeypatch.setattr(settings, "WEBINAR_CHAT_INVITE_URL", "https://t.me/ai_story_news")
    assert chat_invite_url_for("self") == "https://t.me/ai_story_news"
    monkeypatch.setattr(settings, "SELF_CHAT_INVITE_URL", "https://t.me/+T5nhnyDyzhhmZjUy")
    assert chat_invite_url_for("self") == "https://t.me/+T5nhnyDyzhhmZjUy"


@pytest.mark.asyncio
async def test_cabinet_shows_self_chat_button(client, db_session, story_course, user, monkeypatch):
    monkeypatch.setattr(settings, "SELF_CHAT_INVITE_URL", "https://t.me/+T5nhnyDyzhhmZjUy")
    await grant_course_access(db_session, user, story_course.id, "self", commit=True)
    await _login(client, db_session, user)
    html = (await client.get("/cabinet/lessons")).text
    assert 'href="https://t.me/+T5nhnyDyzhhmZjUy"' in html
    assert "Войти в чат учеников" in html
