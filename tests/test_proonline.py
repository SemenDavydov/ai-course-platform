"""ProOnline installment: signature + webhook access grant."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course, Tariff
from app.models.payment import Payment
from app.models.user import User
from app.models.user_session import UserSession
from app.models.webhook_event import WebhookEvent
from app.services.access import get_access
from app.services.proonline import ProOnlineClient, verify_webhook_signature


def _sign_standard(secret: str, body: bytes, msg_id: str, timestamp: str) -> str:
    raw = secret[len("whsec_") :]
    key = base64.b64decode(raw)
    to_sign = f"{msg_id}.{timestamp}.".encode("utf-8") + body
    digest = hmac.new(key, to_sign, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


@pytest_asyncio.fixture
async def published_course(db_session: AsyncSession) -> Course:
    course = Course(
        title="AI STORY: воплоти свою историю",
        description="Новый курс",
        price=9990.0,
        is_published=True,
        slug="ai-story",
        sort_order=0,
        is_legacy=False,
    )
    db_session.add(course)
    await db_session.flush()
    db_session.add_all(
        [
            Tariff(
                course_id=course.id,
                slug="pro",
                name="Pro",
                price=9990.0,
                old_price=12990.0,
                is_active=True,
                sort_order=0,
            ),
            Tariff(
                course_id=course.id,
                slug="vip",
                name="VIP",
                price=29990.0,
                old_price=34990.0,
                is_active=True,
                sort_order=1,
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(course)
    return course


def test_verify_webhook_signature_standard_webhooks():
    secret = "whsec_" + base64.b64encode(b"test-secret-bytes-32!!!!!!!!!!!!").decode()
    body = b'{"event":"payment.status_changed","event_id":"evt_1"}'
    msg_id = "msg_123"
    ts = str(int(time.time()))
    sig = _sign_standard(secret, body, msg_id, ts)
    assert verify_webhook_signature(
        body=body,
        headers={
            "webhook-id": msg_id,
            "webhook-timestamp": ts,
            "webhook-signature": sig,
        },
        secret=secret,
    )


def test_verify_webhook_signature_rejects_bad_sig():
    secret = "whsec_" + base64.b64encode(b"test-secret-bytes-32!!!!!!!!!!!!").decode()
    body = b'{"event":"payment.status_changed"}'
    assert not verify_webhook_signature(
        body=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": str(int(time.time())),
            "webhook-signature": "v1,AAAA",
        },
        secret=secret,
    )


@pytest.mark.asyncio
async def test_proonline_webhook_grants_access_on_paid(
    client: AsyncClient,
    db_session: AsyncSession,
    published_course,
):
    from app.config import settings

    user = User(
        email="installment@example.com",
        email_verified=True,
        has_access=False,
        accepted_offer=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()

    order_id = f"po_{user.id}_pro_testorder01"
    payment = Payment(
        user_id=user.id,
        amount=9990,
        payment_id=order_id,
        status="pending",
        course_id=published_course.id,
        tariff_slug="pro",
        provider="proonline",
    )
    db_session.add(payment)
    await db_session.commit()

    secret = "whsec_" + base64.b64encode(b"test-secret-bytes-32!!!!!!!!!!!!").decode()
    payload = {
        "event": "payment.status_changed",
        "event_id": "evt_paid_1",
        "status": "paid",
        "external_order_id": order_id,
        "application_number": "A-1",
        "amount": 9990,
        "seller_commission": 0,
        "bank_commission": 0,
        "credit_contract_amount": 9990,
        "seller_payout_amount": 9990,
        "buyer": {"email": user.email, "full_name": "Test", "phone": "+7000"},
        "payment_method": None,
        "requested_payment_method": None,
        "provider_payment_method": None,
        "actual_payment_method": "installment",
        "payment_method_source": "provider",
        "installment_term": 6,
        "allowed_installment_terms": [3, 6, 12],
        "direct_bank_mode": "installment",
        "payment_url": None,
        "crm": {
            "checkout_id": 1,
            "application_id": 1,
            "deal_id": 1,
            "payment_id": 1,
            "school_id": 34511,
            "product_id": None,
        },
        "occurred_at": "2026-09-15T10:00:00Z",
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    msg_id = "msg_paid_1"
    ts = str(int(time.time()))
    sig = _sign_standard(secret, raw, msg_id, ts)

    with patch.object(settings, "PROONLINE_WEBHOOK_SECRET", secret), patch(
        "app.api.webhooks._notify_purchase", new=AsyncMock()
    ):
        resp = await client.post(
            "/webhooks/proonline",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "webhook-id": msg_id,
                "webhook-timestamp": ts,
                "webhook-signature": sig,
            },
        )
    assert resp.status_code == 200

    await db_session.refresh(payment)
    assert payment.status == "succeeded"
    access = await get_access(db_session, user.id, published_course.id)
    assert access is not None
    assert access.tariff_slug == "pro"

    with patch.object(settings, "PROONLINE_WEBHOOK_SECRET", secret), patch(
        "app.api.webhooks._notify_purchase", new=AsyncMock()
    ):
        resp2 = await client.post(
            "/webhooks/proonline",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "webhook-id": msg_id,
                "webhook-timestamp": ts,
                "webhook-signature": sig,
            },
        )
    assert resp2.status_code == 200
    events = (
        await db_session.execute(
            select(WebhookEvent).where(WebhookEvent.event_id == "evt_paid_1")
        )
    ).scalars().all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_create_installment_session(
    client: AsyncClient,
    db_session: AsyncSession,
    published_course,
):
    user = User(
        email="buyer-installment@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()
    token = f"session_token_{user.id}"
    db_session.add(
        UserSession(
            user_id=user.id,
            session_token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    await db_session.commit()

    fake = {
        "external_order_id": "po_x",
        "questionnaire_url": "https://crm.procourse.online/q/test",
        "session_status": "prepared",
        "payment_status": None,
        "is_paid": False,
        "expires_at": "2026-09-16T00:00:00Z",
    }

    async def _fake_create(self, **kwargs):
        return fake

    with patch.object(
        ProOnlineClient, "configured", new=property(lambda self: True)
    ), patch.object(ProOnlineClient, "create_payment_form_session", new=_fake_create):
        resp = await client.post(
            "/api/v1/payments/installment",
            json={"accepted_offer": True, "tariff_slug": "pro"},
            cookies={"user_session": token},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["questionnaire_url"].startswith("https://")
    row = (
        await db_session.execute(select(Payment).where(Payment.provider == "proonline"))
    ).scalar_one()
    assert row.status == "pending"
    assert row.tariff_slug == "pro"
