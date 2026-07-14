"""
Payments router: создание платежа через YooKassa для веб-пользователей.
Prefix: /api/v1/payments
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.course import Course
from app.models.payment import Payment
from app.services.auth import get_current_user
from app.services.payment import PaymentService
from app.config import settings

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

_payment_service = PaymentService()


class CreatePaymentResponse(BaseModel):
    confirmation_url: str


class CreatePaymentRequest(BaseModel):
    accepted_offer: bool = False


@router.post("/create", response_model=CreatePaymentResponse)
async def create_payment(
    body: CreatePaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Создаёт платёж в YooKassa для авторизованного веб-пользователя.
    Требования: accepted_offer=True, has_access=False.
    """
    if not current_user.accepted_offer:
        if not body.accepted_offer:
            raise HTTPException(status_code=403, detail="Необходимо принять оферту")
        current_user.accepted_offer = True
        await db.commit()
        await db.refresh(current_user)
    if current_user.has_access:
        raise HTTPException(status_code=400, detail="У вас уже есть доступ к курсу")

    # Берём первый опубликованный курс
    result = await db.execute(select(Course).where(Course.is_published == True))
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Курс не найден")

    return_url = f"{settings.SITE_URL}/payment/success"

    payment_data = await _payment_service.create_payment(
        user=current_user,
        amount=course.price,
        description=f"Оплата курса «{course.title}»",
        course_id=course.id,
        return_url=return_url,
    )
    if not payment_data:
        raise HTTPException(status_code=502, detail="Ошибка при создании платежа, попробуйте позже")

    # Сохраняем pending-запись в БД
    pending = Payment(
        user_id=current_user.id,
        amount=course.price,
        payment_id=payment_data["payment_id"],
        status="pending",
        description=f"Оплата курса «{course.title}»",
    )
    db.add(pending)
    await db.commit()

    return {"confirmation_url": payment_data["confirmation_url"]}
