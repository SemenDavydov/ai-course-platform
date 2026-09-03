"""
Payments router: создание платежа через YooKassa для веб-пользователей.
Prefix: /api/v1/payments
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.course import Course, Tariff
from app.models.payment import Payment
from app.services.auth import get_current_user
from app.services.payment import PaymentService
from app.services.access import get_access, get_primary_course
from app.config import settings

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

_payment_service = PaymentService()


class CreatePaymentResponse(BaseModel):
    confirmation_url: str


class CreatePaymentRequest(BaseModel):
    accepted_offer: bool = False
    tariff_slug: str = Field(..., description="pro or vip")
    course_slug: str | None = None


@router.post("/create", response_model=CreatePaymentResponse)
async def create_payment(
    body: CreatePaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Создаёт платёж в YooKassa.
    Требования: accepted_offer, тариф pro|vip.
    Повторная покупка того же тарифа запрещена; Pro→VIP разрешён.
    """
    tariff_slug = (body.tariff_slug or "").strip().lower()
    if tariff_slug not in ("pro", "vip"):
        raise HTTPException(status_code=400, detail="Укажите тариф: pro или vip")

    if not current_user.accepted_offer:
        if not body.accepted_offer:
            raise HTTPException(status_code=403, detail="Необходимо принять оферту")
        current_user.accepted_offer = True
        await db.commit()
        await db.refresh(current_user)

    if body.course_slug:
        result = await db.execute(
            select(Course)
            .where(Course.slug == body.course_slug, Course.is_published == True)
            .options(selectinload(Course.tariffs))
        )
        course = result.scalar_one_or_none()
    else:
        course = await get_primary_course(db)
        if course:
            result = await db.execute(
                select(Course)
                .where(Course.id == course.id)
                .options(selectinload(Course.tariffs))
            )
            course = result.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Курс не найден")

    tariff = next(
        (t for t in course.tariffs if t.slug == tariff_slug and t.is_active), None
    )
    if not tariff:
        # Fallback query if tariffs not loaded / empty relation
        t_result = await db.execute(
            select(Tariff).where(
                Tariff.course_id == course.id,
                Tariff.slug == tariff_slug,
                Tariff.is_active == True,
            )
        )
        tariff = t_result.scalar_one_or_none()
    if not tariff:
        raise HTTPException(status_code=404, detail="Тариф не найден")

    existing = await get_access(db, current_user.id, course.id)
    if existing:
        if existing.tariff_slug == "vip":
            raise HTTPException(status_code=400, detail="У вас уже есть VIP-доступ к этому курсу")
        if existing.tariff_slug == tariff_slug:
            raise HTTPException(status_code=400, detail="У вас уже есть доступ по этому тарифу")
        if existing.tariff_slug == "pro" and tariff_slug != "vip":
            raise HTTPException(status_code=400, detail="У вас уже есть Pro-доступ. Доступен апгрейд до VIP")

    return_url = f"{settings.SITE_URL}/payment/success"
    description = f"Оплата курса «{course.title}» — тариф {tariff.name}"

    payment_data = await _payment_service.create_payment(
        user=current_user,
        amount=tariff.price,
        description=description,
        course_id=course.id,
        return_url=return_url,
        tariff_slug=tariff.slug,
        tariff_id=tariff.id,
    )
    if not payment_data:
        raise HTTPException(
            status_code=502, detail="Ошибка при создании платежа, попробуйте позже"
        )

    pending = Payment(
        user_id=current_user.id,
        amount=tariff.price,
        payment_id=payment_data["payment_id"],
        status="pending",
        description=description,
        course_id=course.id,
        tariff_id=tariff.id,
        tariff_slug=tariff.slug,
    )
    db.add(pending)
    await db.commit()

    return {"confirmation_url": payment_data["confirmation_url"]}
