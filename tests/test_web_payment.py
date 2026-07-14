"""
Тесты Этапа 1.6: создание платежа веб-пользователем,
webhook → email вместо Telegram для веб-пользователей.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.payment import Payment
from app.models.user_session import UserSession
from app.models.course import Course


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def published_course(db_session: AsyncSession) -> Course:
    """Опубликованный курс."""
    course = Course(
        title="ИИ анимации",
        description="Экспресс курс",
        price=2990.0,
        is_published=True,
    )
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)
    return course


@pytest_asyncio.fixture
async def web_user(db_session: AsyncSession) -> User:
    """Верифицированный веб-пользователь, принял оферту, нет доступа."""
    user = User(
        email="buyer@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def web_user_with_access(db_session: AsyncSession) -> User:
    """Веб-пользователь, у которого уже есть доступ."""
    user = User(
        email="hasaccess@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def unverified_user(db_session: AsyncSession) -> User:
    """Веб-пользователь с неподтверждённым email."""
    user = User(
        email="unverified@test.com",
        email_verified=False,
        registration_source="web",
        accepted_offer=True,
        has_access=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def bot_user(db_session: AsyncSession) -> User:
    """Telegram-пользователь с email и telegram_id."""
    user = User(
        telegram_id=111222333,
        email="botuser@test.com",
        email_verified=True,
        registration_source="bot_migrated",
        accepted_offer=True,
        has_access=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _make_session(db_session: AsyncSession, user: User) -> str:
    """Создаёт UserSession и возвращает токен."""
    token = f"session_token_{user.id}"
    session = UserSession(
        user_id=user.id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    await db_session.commit()
    return token


# ---------------------------------------------------------------------------
# POST /api/v1/payments/create
# ---------------------------------------------------------------------------

@patch("app.api.v1.payments._payment_service.create_payment", new_callable=AsyncMock)
async def test_create_payment_success(
    mock_create,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
    published_course: Course,
):
    """Авторизованный верифицированный пользователь → получает confirmation_url."""
    mock_create.return_value = {
        "payment_id": "yoo_pay_123",
        "confirmation_url": "https://yookassa.ru/checkout/payments/yoo_pay_123",
        "status": "pending",
    }
    token = await _make_session(db_session, web_user)

    response = await client.post(
        "/api/v1/payments/create",
        json={},
        cookies={"user_session": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert "confirmation_url" in data
    assert data["confirmation_url"].startswith("https://yookassa.ru")

    # Pending-запись сохранена в БД
    result = await db_session.execute(
        select(Payment).where(Payment.payment_id == "yoo_pay_123")
    )
    payment = result.scalar_one()
    assert payment.status == "pending"
    assert payment.user_id == web_user.id
    assert payment.amount == published_course.price

    # create_payment вызван с правильным return_url
    call_kwargs = mock_create.call_args
    assert "/payment/success" in call_kwargs.kwargs.get("return_url", "")


@patch("app.api.v1.payments._payment_service.create_payment", new_callable=AsyncMock)
async def test_create_payment_unverified_email(
    mock_create,
    client: AsyncClient,
    db_session: AsyncSession,
    unverified_user: User,
    published_course: Course,
):
    """Неподтверждённый email НЕ блокирует оплату (доступ к курсу = факт платежа)."""
    mock_create.return_value = {
        "payment_id": "yoo_pay_unverified_1",
        "confirmation_url": "https://yookassa.ru/checkout/payments/yoo_pay_unverified_1",
        "status": "pending",
    }
    token = await _make_session(db_session, unverified_user)

    response = await client.post(
        "/api/v1/payments/create",
        json={},
        cookies={"user_session": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confirmation_url"].startswith("https://yookassa.ru")
    mock_create.assert_called_once()


@patch("app.api.v1.payments._payment_service.create_payment", new_callable=AsyncMock)
async def test_create_payment_no_offer(
    mock_create,
    client: AsyncClient,
    db_session: AsyncSession,
    published_course: Course,
):
    """Пользователь без принятой оферты → 403."""
    user = User(
        email="nooffer2@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    token = await _make_session(db_session, user)

    response = await client.post(
        "/api/v1/payments/create",
        json={},
        cookies={"user_session": token},
    )
    assert response.status_code == 403
    mock_create.assert_not_called()


@patch("app.api.v1.payments._payment_service.create_payment", new_callable=AsyncMock)
async def test_create_payment_already_has_access(
    mock_create,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user_with_access: User,
    published_course: Course,
):
    """Пользователь уже купил курс → 400."""
    token = await _make_session(db_session, web_user_with_access)

    response = await client.post(
        "/api/v1/payments/create",
        json={},
        cookies={"user_session": token},
    )
    assert response.status_code == 400
    mock_create.assert_not_called()


async def test_create_payment_unauthenticated(
    client: AsyncClient,
    published_course: Course,
):
    """Без авторизации → 401."""
    response = await client.post("/api/v1/payments/create", json={})
    assert response.status_code == 401


@patch("app.api.v1.payments._payment_service.create_payment", new_callable=AsyncMock)
async def test_create_payment_yookassa_error(
    mock_create,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
    published_course: Course,
):
    """YooKassa вернул None (ошибка) → 502."""
    mock_create.return_value = None
    token = await _make_session(db_session, web_user)

    response = await client.post(
        "/api/v1/payments/create",
        json={},
        cookies={"user_session": token},
    )
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# POST /webhooks/yookassa — логика уведомлений
# ---------------------------------------------------------------------------

def _webhook_body(user_id: int, payment_id: str = "pay_webhook_001") -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "2990.00", "currency": "RUB"},
            "description": "Оплата курса",
            "metadata": {"user_id": user_id},
        },
    }


@patch("app.tasks.send_email_task.delay")
@patch("app.api.webhooks.bot.send_message", new_callable=AsyncMock)
async def test_webhook_web_user_sends_email(
    mock_tg,
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
    yookassa_api,
):
    """Webhook для web-пользователя (без telegram_id) → email отправлен, Telegram НЕ вызван."""
    # Сохраняем pending-платёж
    payment = Payment(
        user_id=web_user.id,
        amount=2990.0,
        payment_id="pay_web_001",
        status="pending",
    )
    db_session.add(payment)
    await db_session.commit()

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(web_user.id, "pay_web_001"),
    )
    assert response.status_code == 200

    # Пользователь получил доступ
    await db_session.refresh(web_user)
    assert web_user.has_access is True

    # Email отправлен, Telegram не вызван
    mock_email.assert_called_once()
    mock_tg.assert_not_called()


@patch("app.tasks.send_email_task.delay")
@patch("app.api.webhooks.bot.send_message", new_callable=AsyncMock)
async def test_webhook_bot_user_sends_telegram(
    mock_tg,
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    bot_user: User,
    yookassa_api,
):
    """
    BOT_ENABLED=False (Этап 3): даже бот-пользователи получают email, Telegram НЕ вызывается.
    Для тестирования legacy Telegram-пути нужно явно патчить settings.BOT_ENABLED=True.
    """
    payment = Payment(
        user_id=bot_user.id,
        amount=2990.0,
        payment_id="pay_bot_001",
        status="pending",
    )
    db_session.add(payment)
    await db_session.commit()

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(bot_user.id, "pay_bot_001"),
    )
    assert response.status_code == 200

    await db_session.refresh(bot_user)
    assert bot_user.has_access is True

    # BOT_ENABLED=False → email отправлен, Telegram НЕ вызван
    mock_email.assert_called_once()
    mock_tg.assert_not_called()


@patch("app.tasks.send_email_task.delay")
@patch("app.api.webhooks.bot.send_message", new_callable=AsyncMock)
@patch("app.api.webhooks.settings.BOT_ENABLED", True)
async def test_webhook_bot_user_sends_telegram_when_bot_enabled(
    mock_tg,
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    bot_user: User,
    yookassa_api,
):
    """Legacy: BOT_ENABLED=True → бот-пользователь получает Telegram-сообщение, email НЕ отправлен."""
    payment = Payment(
        user_id=bot_user.id,
        amount=2990.0,
        payment_id="pay_bot_002",
        status="pending",
    )
    db_session.add(payment)
    await db_session.commit()

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(bot_user.id, "pay_bot_002"),
    )
    assert response.status_code == 200

    await db_session.refresh(bot_user)
    assert bot_user.has_access is True

    mock_tg.assert_called_once()
    mock_email.assert_not_called()


@patch("app.tasks.send_email_task.delay")
@patch("app.api.webhooks.bot.send_message", new_callable=AsyncMock)
async def test_webhook_ignored_event(mock_tg, mock_email, client: AsyncClient):
    """Не-succeeded событие игнорируется — доступ не выдаётся."""
    response = await client.post(
        "/webhooks/yookassa",
        json={"event": "payment.waiting_for_capture", "object": {}},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Event ignored"
    mock_tg.assert_not_called()
    mock_email.assert_not_called()


@patch("app.tasks.send_email_task.delay")
@patch("app.api.webhooks.bot.send_message", new_callable=AsyncMock)
async def test_webhook_already_processed(
    mock_tg,
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
):
    """Повторный webhook для уже обработанного платежа → 200, доступ не меняется."""
    payment = Payment(
        user_id=web_user.id,
        amount=2990.0,
        payment_id="pay_dup_001",
        status="succeeded",  # уже обработан
        paid_at=datetime.utcnow(),
    )
    db_session.add(payment)
    web_user.has_access = True
    db_session.add(web_user)
    await db_session.commit()

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(web_user.id, "pay_dup_001"),
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Already processed"

    mock_tg.assert_not_called()
    mock_email.assert_not_called()


# ---------------------------------------------------------------------------
# POST /webhooks/yookassa — защита от подделки
# ---------------------------------------------------------------------------

async def test_webhook_rejects_untrusted_ip(client: AsyncClient, web_user: User):
    """Запрос не с IP ЮKassa → 403, доступ не выдаётся."""
    from app.main import app
    from app.api.webhooks import verify_yookassa_source

    override = app.dependency_overrides.pop(verify_yookassa_source)
    try:
        response = await client.post(
            "/webhooks/yookassa",
            json=_webhook_body(web_user.id, "pay_spoofed"),
        )
    finally:
        app.dependency_overrides[verify_yookassa_source] = override

    assert response.status_code == 403


@patch("app.tasks.send_email_task.delay")
async def test_webhook_unknown_payment_denies_access(
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
    yookassa_api,
):
    """Платёж, которого мы не создавали, не выдаёт доступ и не создаёт запись в БД."""
    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(web_user.id, "pay_never_created"),
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Unknown payment ignored"

    await db_session.refresh(web_user)
    assert web_user.has_access is False

    result = await db_session.execute(
        select(Payment).where(Payment.payment_id == "pay_never_created")
    )
    assert result.scalar_one_or_none() is None
    mock_email.assert_not_called()


@patch("app.tasks.send_email_task.delay")
async def test_webhook_amount_mismatch_denies_access(
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
    yookassa_api,
):
    """Сумма в ЮKassa не совпадает с нашей записью → доступ не выдаётся."""
    db_session.add(Payment(
        user_id=web_user.id,
        amount=2990.0,
        payment_id="pay_amount_mismatch",
        status="pending",
    ))
    await db_session.commit()

    yookassa_api.set_payment(status="succeeded", amount="1.00")

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(web_user.id, "pay_amount_mismatch"),
    )
    assert response.status_code == 400

    await db_session.refresh(web_user)
    assert web_user.has_access is False
    mock_email.assert_not_called()


@patch("app.tasks.send_email_task.delay")
async def test_webhook_not_succeeded_in_api_denies_access(
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
    yookassa_api,
):
    """Тело говорит succeeded, а API ЮKassa — pending → верим API, доступ не выдаётся."""
    db_session.add(Payment(
        user_id=web_user.id,
        amount=2990.0,
        payment_id="pay_still_pending",
        status="pending",
    ))
    await db_session.commit()

    yookassa_api.set_payment(status="pending", amount="2990.00")

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(web_user.id, "pay_still_pending"),
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Payment not succeeded"

    await db_session.refresh(web_user)
    assert web_user.has_access is False
    mock_email.assert_not_called()


# ---------------------------------------------------------------------------
# GET /payment/success
# ---------------------------------------------------------------------------

async def test_payment_success_page_succeeded(
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
):
    """Страница /payment/success при succeeded-платеже показывает статус succeeded."""
    web_user.has_access = True
    db_session.add(web_user)
    payment = Payment(
        user_id=web_user.id,
        amount=2990.0,
        payment_id="pay_success_page",
        status="succeeded",
        paid_at=datetime.utcnow(),
    )
    db_session.add(payment)
    await db_session.commit()

    token = await _make_session(db_session, web_user)
    response = await client.get(
        "/payment/success",
        cookies={"user_session": token},
    )
    assert response.status_code == 200
    assert "Оплата прошла" in response.text


async def test_payment_success_page_pending(
    client: AsyncClient,
    db_session: AsyncSession,
    web_user: User,
):
    """Страница /payment/success при pending-платеже показывает 'Обрабатывается'."""
    payment = Payment(
        user_id=web_user.id,
        amount=2990.0,
        payment_id="pay_pending_page",
        status="pending",
    )
    db_session.add(payment)
    await db_session.commit()

    token = await _make_session(db_session, web_user)
    response = await client.get(
        "/payment/success",
        cookies={"user_session": token},
    )
    assert response.status_code == 200
    assert "Обрабатывается" in response.text


async def test_payment_failure_page(client: AsyncClient):
    """Страница /payment/failure доступна без авторизации."""
    response = await client.get("/payment/failure")
    assert response.status_code == 200
    assert "Попробовать снова" in response.text
