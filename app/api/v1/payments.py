"""
Payments router: создание платежа через YooKassa / рассрочка ProOnline.
Prefix: /api/v1/payments
"""
import json
import uuid

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
from app.services.proonline import ProOnlineClient, ProOnlineError
from app.config import settings

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

_payment_service = PaymentService()


class CreatePaymentResponse(BaseModel):
    confirmation_url: str


class CreatePaymentRequest(BaseModel):
    accepted_offer: bool = False
    tariff_slug: str = Field(..., description="pro or vip")
    course_slug: str | None = None


class CreateInstallmentResponse(BaseModel):
    questionnaire_url: str
    external_order_id: str


async def _resolve_course_and_tariff(
    db: AsyncSession,
    *,
    tariff_slug: str,
    course_slug: str | None,
) -> tuple[Course, Tariff]:
    if course_slug:
        result = await db.execute(
            select(Course)
            .where(Course.slug == course_slug, Course.is_published == True)
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
    return course, tariff


def _ensure_can_purchase(existing, tariff_slug: str) -> None:
    if not existing:
        return
    if existing.tariff_slug == "vip":
        raise HTTPException(status_code=400, detail="У вас уже есть VIP-доступ к этому курсу")
    if existing.tariff_slug == tariff_slug:
        raise HTTPException(status_code=400, detail="У вас уже есть доступ по этому тарифу")
    if existing.tariff_slug == "pro" and tariff_slug != "vip":
        raise HTTPException(
            status_code=400,
            detail="У вас уже есть Pro-доступ. Доступен апгрейд до VIP",
        )


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

    course, tariff = await _resolve_course_and_tariff(
        db, tariff_slug=tariff_slug, course_slug=body.course_slug
    )
    existing = await get_access(db, current_user.id, course.id)
    _ensure_can_purchase(existing, tariff_slug)

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
        provider="yookassa",
    )
    db.add(pending)
    await db.commit()

    return {"confirmation_url": payment_data["confirmation_url"]}


@router.post("/installment", response_model=CreateInstallmentResponse)
async def create_installment(
    body: CreatePaymentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Создаёт персональную анкету ProOnline (рассрочка) и возвращает questionnaire_url.
    """
    tariff_slug = (body.tariff_slug or "").strip().lower()
    if tariff_slug not in ("pro", "vip"):
        raise HTTPException(status_code=400, detail="Укажите тариф: pro или vip")

    client = ProOnlineClient()
    if not client.configured:
        raise HTTPException(
            status_code=503,
            detail="Рассрочка временно недоступна. Оплатите картой или напишите нам.",
        )

    if not current_user.accepted_offer:
        if not body.accepted_offer:
            raise HTTPException(status_code=403, detail="Необходимо принять оферту")
        current_user.accepted_offer = True
        await db.commit()
        await db.refresh(current_user)

    course, tariff = await _resolve_course_and_tariff(
        db, tariff_slug=tariff_slug, course_slug=body.course_slug
    )
    existing = await get_access(db, current_user.id, course.id)
    _ensure_can_purchase(existing, tariff_slug)

    external_order_id = f"po_{current_user.id}_{tariff.slug}_{uuid.uuid4().hex[:12]}"
    title = f"{course.title} — {tariff.name}"
    description = f"Рассрочка ProOnline «{course.title}» — тариф {tariff.name}"

    try:
        session = await client.create_payment_form_session(
            external_order_id=external_order_id,
            title=title,
            amount=float(tariff.price),
        )
    except ProOnlineError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc) or "Ошибка создания анкеты рассрочки",
        ) from exc

    questionnaire_url = session["questionnaire_url"]
    pending = Payment(
        user_id=current_user.id,
        amount=tariff.price,
        payment_id=external_order_id,
        status="pending",
        description=description,
        course_id=course.id,
        tariff_id=tariff.id,
        tariff_slug=tariff.slug,
        provider="proonline",
        receipt_data=json.dumps(
            {
                "questionnaire_url": questionnaire_url,
                "session_status": session.get("session_status"),
                "expires_at": session.get("expires_at"),
                "school_id": settings.PROONLINE_SCHOOL_ID,
                "form_id": settings.PROONLINE_FORM_ID,
            },
            ensure_ascii=False,
        ),
    )
    db.add(pending)
    await db.commit()

    return {
        "questionnaire_url": questionnaire_url,
        "external_order_id": external_order_id,
    }
