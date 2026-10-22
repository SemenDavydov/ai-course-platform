"""bcrypt и SDK ЮKassa не должны блокировать event loop (иначе поток запросов кладёт воркер)."""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from app.models import user as user_module
from app.models.user import User


async def _ticks_while(coro, *, interval: float = 0.01) -> tuple[object, int]:
    """Сколько раз event loop успел «тикнуть», пока выполнялась корутина."""
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(interval)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        done.set()
        await t
    return result, ticks


@pytest.mark.asyncio
async def test_acheck_password_does_not_block_loop(monkeypatch):
    user = User(email="a@example.com")
    user.set_password("password123")

    real_verify = user_module._verify_password

    def slow_verify(password, password_hash):
        time.sleep(0.3)  # имитация тяжёлого bcrypt
        return real_verify(password, password_hash)

    monkeypatch.setattr(user_module, "_verify_password", slow_verify)

    ok, ticks = await _ticks_while(user.acheck_password("password123"))
    assert ok is True
    assert ticks >= 10  # при блокировке было бы 0–1

    bad, _ = await _ticks_while(user.acheck_password("wrong-password"))
    assert bad is False


@pytest.mark.asyncio
async def test_aset_password_does_not_block_loop(monkeypatch):
    real_hash = user_module._hash_password

    def slow_hash(password):
        time.sleep(0.3)
        return real_hash(password)

    monkeypatch.setattr(user_module, "_hash_password", slow_hash)

    user = User(email="b@example.com")
    _, ticks = await _ticks_while(user.aset_password("newpassword123"))
    assert ticks >= 10
    assert user.check_password("newpassword123")


@pytest.mark.asyncio
async def test_yookassa_create_does_not_block_loop():
    from app.services.payment import PaymentService

    def slow_create(params, idempotence_key):
        time.sleep(0.3)  # имитация медленного ответа ЮKassa
        p = MagicMock()
        p.id = "pay_1"
        p.status = "pending"
        p.confirmation.confirmation_url = "https://yookassa.example/confirm"
        return p

    user = User(id=1, email="c@example.com")
    with patch("app.services.payment.YooPayment.create", side_effect=slow_create):
        data, ticks = await _ticks_while(
            PaymentService().create_payment(
                user=user, amount=8990, description="test", course_id=1
            )
        )
    assert data["payment_id"] == "pay_1"
    assert ticks >= 10
