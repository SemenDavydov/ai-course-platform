import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.models.payment import Payment
from datetime import datetime


@pytest.mark.asyncio
async def test_webhook_successful_payment(client: AsyncClient, db_session: AsyncSession):
    # Создаем тестового пользователя
    user = User(
        telegram_id=123456789,
        username="test_user",
        email="test@example.com",
        has_access=False
    )
    db_session.add(user)
    await db_session.commit()

    # Мокаем вебхук от ЮKassa
    webhook_data = {
        "event": "payment.succeeded",
        "object": {
            "id": "test_payment_123",
            "status": "succeeded",
            "amount": {
                "value": "5000.00",
                "currency": "RUB"
            },
            "description": "Оплата курса",
            "metadata": {
                "user_id": user.id
            }
        }
    }

    response = await client.post("/webhooks/yookassa", json=webhook_data)

    assert response.status_code == 200

    # Проверяем, что пользователь получил доступ
    await db_session.refresh(user)
    assert user.has_access == True

    # Проверяем, что платеж создан
    from sqlalchemy import select
    query = select(Payment).where(Payment.payment_id == "test_payment_123")
    result = await db_session.execute(query)
    payment = result.scalar_one()

    assert payment.status == "succeeded"
    assert payment.amount == 5000.0


@pytest.mark.asyncio
async def test_webhook_ignored_event(client: AsyncClient):
    # Тест на игнорирование других событий
    webhook_data = {
        "event": "payment.waiting_for_capture",
        "object": {}
    }

    response = await client.post("/webhooks/yookassa", json=webhook_data)
    assert response.status_code == 200
    assert response.json()["message"] == "Event ignored"