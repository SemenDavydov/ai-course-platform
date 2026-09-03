"""Smoke tests for multicourse entitlements and tariffs."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.user_session import UserSession
from app.models.course import Course, Module, Tariff, UserCourseAccess, Lesson
from app.models.payment import Payment
from app.services.access import grant_course_access, user_has_course


@pytest_asyncio.fixture
async def story_course(db_session: AsyncSession) -> Course:
    course = Course(
        title="AI STORY",
        description="new",
        price=9990,
        is_published=True,
        slug="ai-story",
        sort_order=0,
        is_legacy=False,
    )
    db_session.add(course)
    await db_session.flush()
    db_session.add_all(
        [
            Tariff(course_id=course.id, slug="pro", name="Pro", price=9990, is_active=True),
            Tariff(course_id=course.id, slug="vip", name="VIP", price=29990, is_active=True),
        ]
    )
    mod = Module(course_id=course.id, title="Введение", order=1, button_label="Модуль 1")
    db_session.add(mod)
    await db_session.flush()
    db_session.add(
        Lesson(
            course_id=course.id,
            module_id=mod.id,
            title="Урок 1",
            order=101,
            video_id="pending",
        )
    )
    await db_session.commit()
    await db_session.refresh(course)
    return course


@pytest_asyncio.fixture
async def legacy_course(db_session: AsyncSession) -> Course:
    course = Course(
        title="Классический",
        description="old",
        price=2990,
        is_published=True,
        slug="ai-animations",
        sort_order=100,
        is_legacy=True,
    )
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)
    return course


async def test_legacy_user_cannot_see_story_without_purchase(
    db_session: AsyncSession,
    story_course: Course,
    legacy_course: Course,
):
    user = User(email="legacy@test.com", has_access=True, registration_source="web")
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()
    await grant_course_access(db_session, user, legacy_course.id, "legacy", commit=True)

    assert await user_has_course(db_session, user, legacy_course.id)
    assert not await user_has_course(db_session, user, story_course.id)


@patch("app.api.v1.payments._payment_service.create_payment", new_callable=AsyncMock)
async def test_upgrade_pro_to_vip_allowed(
    mock_create,
    client: AsyncClient,
    db_session: AsyncSession,
    story_course: Course,
):
    mock_create.return_value = {
        "payment_id": "yoo_vip_up",
        "confirmation_url": "https://yookassa.ru/checkout/vip",
        "status": "pending",
    }
    user = User(
        email="upgrade@test.com",
        email_verified=True,
        accepted_offer=True,
        registration_source="web",
        has_access=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()
    await grant_course_access(db_session, user, story_course.id, "pro", commit=True)

    token = "upgrade_session"
    db_session.add(
        UserSession(
            user_id=user.id,
            session_token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    await db_session.commit()

    response = await client.post(
        "/api/v1/payments/create",
        json={"tariff_slug": "vip"},
        cookies={"user_session": token},
    )
    assert response.status_code == 200


@patch("app.tasks.send_email")
async def test_webhook_grants_story_entitlement(
    mock_email,
    client: AsyncClient,
    db_session: AsyncSession,
    story_course: Course,
    yookassa_api,
):
    user = User(
        email="storybuyer@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
        has_access=False,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.flush()

    payment = Payment(
        user_id=user.id,
        amount=9990.0,
        payment_id="pay_story_001",
        status="pending",
        course_id=story_course.id,
        tariff_slug="pro",
    )
    db_session.add(payment)
    await db_session.commit()

    response = await client.post(
        "/webhooks/yookassa",
        json={
            "event": "payment.succeeded",
            "object": {
                "id": "pay_story_001",
                "status": "succeeded",
                "amount": {"value": "9990.00", "currency": "RUB"},
                "metadata": {
                    "user_id": user.id,
                    "course_id": story_course.id,
                    "tariff_slug": "pro",
                },
            },
        },
    )
    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.has_access is True
    assert await user_has_course(db_session, user, story_course.id)
    access = (
        await db_session.execute(
            select(UserCourseAccess).where(
                UserCourseAccess.user_id == user.id,
                UserCourseAccess.course_id == story_course.id,
            )
        )
    ).scalar_one()
    assert access.tariff_slug == "pro"
    mock_email.assert_called()
