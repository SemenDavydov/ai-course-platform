import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.models.payment import Payment
from datetime import datetime


def _webhook_body(user_id: int, payment_id: str, amount: str = "5000.00") -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "description": "Оплата курса",
            "metadata": {"user_id": user_id},
        },
    }


@pytest.mark.asyncio
async def test_webhook_successful_payment(
    client: AsyncClient,
    db_session: AsyncSession,
    yookassa_api,
):
    """Платёж, созданный нами и подтверждённый API ЮKassa → доступ выдан."""
    user = User(
        telegram_id=123456789,
        username="test_user",
        email="test@example.com",
        has_access=False
    )
    db_session.add(user)
    await db_session.commit()

    # Платёж создаётся при оформлении заказа, до вебхука
    db_session.add(Payment(
        user_id=user.id,
        amount=5000.0,
        payment_id="test_payment_123",
        status="pending",
    ))
    await db_session.commit()

    yookassa_api.set_payment(status="succeeded", amount="5000.00")

    response = await client.post(
        "/webhooks/yookassa",
        json=_webhook_body(user.id, "test_payment_123"),
    )
    assert response.status_code == 200

    await db_session.refresh(user)
    assert user.has_access is True

    query = select(Payment).where(Payment.payment_id == "test_payment_123")
    result = await db_session.execute(query)
    payment = result.scalar_one()

    assert payment.status == "succeeded"
    assert payment.amount == 5000.0
    assert payment.paid_at is not None


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
