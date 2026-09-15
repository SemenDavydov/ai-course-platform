import asyncio
import ipaddress
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging
from datetime import datetime
import json

from yookassa import Payment as YooPayment

from app.database import get_db
from app.models.user import User
from app.models.payment import Payment
from app.models.webhook_event import WebhookEvent
from app.models.course import Course, Tariff
from app.services.payment import PaymentService
from app.services.access import grant_course_access, get_primary_course
from app.services.proonline import verify_webhook_signature
from app.bot.bot import bot
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

YOOKASSA_NETWORKS = [
    ipaddress.ip_network(net)
    for net in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "2a02:5180::/32",
    )
]

AMOUNT_TOLERANCE = 0.01


async def verify_yookassa_source(request: Request) -> None:
    client = request.client
    if client is None:
        logger.warning("Webhook rejected: no client address")
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        ip = ipaddress.ip_address(client.host)
    except ValueError:
        logger.warning("Webhook rejected: unparsable client address %s", client.host)
        raise HTTPException(status_code=403, detail="Forbidden")

    if not any(ip in net for net in YOOKASSA_NETWORKS):
        logger.warning("Webhook rejected: untrusted IP %s", ip)
        raise HTTPException(status_code=403, detail="Forbidden")


async def _fetch_remote_payment(payment_id: str):
    PaymentService()
    try:
        return await asyncio.to_thread(YooPayment.find_one, payment_id)
    except Exception as e:
        logger.error("Failed to fetch payment %s from YooKassa: %s", payment_id, e)
        return None


def _render_email(template_name: str, **ctx) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    jinja = Environment(
        loader=FileSystemLoader("app/templates/emails"),
        autoescape=select_autoescape(["html"]),
    )
    return jinja.get_template(template_name).render(**ctx)


async def _notify_purchase(
    user: User,
    payment: Payment,
    course: Course | None,
    tariff_slug: str,
    db: AsyncSession,
):
    from app.tasks import enqueue_email

    course_title = course.title if course else "курс"
    tariff_name = (tariff_slug or "pro").upper()
    paid_at_str = (payment.paid_at or datetime.utcnow()).strftime("%d.%m.%Y %H:%M")
    if tariff_slug == "pro":
        chat_invite_url = settings.PRO_CHAT_INVITE_URL or None
    elif tariff_slug == "vip":
        chat_invite_url = settings.VIP_CHAT_INVITE_URL or None
    else:
        chat_invite_url = None
    from app.services.auth import create_login_token

    login_token = await create_login_token(db, user)
    cabinet_url = f"{settings.SITE_URL}/auth/open-lessons?token={login_token}"

    use_telegram = (
        settings.BOT_ENABLED
        and user.telegram_id is not None
        and getattr(user, "registration_source", None) != "web"
    )

    if use_telegram:
        welcome_text = (
            f"🎉 <b>Поздравляю с покупкой!</b>\n\n"
            f"✅ Доступ к курсу «{course_title}» ({tariff_name}) открыт"
            + (
                " на 3 месяца после окончания обучения.\n\n"
                if tariff_slug == "pro"
                else " бессрочно.\n\n"
            )
            + "📅 Старт обучения 21.09.2026 в 14:00 МСК.\n\n"
            f"🔐 Видео защищены персональными водяными знаками.\n\n"
            f"📚 Нажмите «Перейти к курсу», чтобы начать обучение."
        )
        if chat_invite_url:
            chat_label = "VIP-канал" if tariff_slug == "vip" else "чат с куратором"
            welcome_text += f"\n\n💬 Закрытый {chat_label}:\n{chat_invite_url}"
        elif tariff_slug == "vip":
            welcome_text += (
                "\n\n⭐ VIP: куратор свяжется с вами для личного чата и Zoom-разборов."
            )

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📖 Перейти к курсу",
                        url=f"https://t.me/{settings.BOT_USERNAME}?start=course",
                    )
                ]
            ]
        )
        if chat_invite_url:
            btn_label = "💬 VIP-канал" if tariff_slug == "vip" else "💬 Чат с куратором"
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text=btn_label, url=chat_invite_url)]
            )
        await bot.send_message(
            chat_id=user.telegram_id,
            text=welcome_text,
            reply_markup=keyboard,
        )
        logger.info("Telegram notification sent to user %s", user.telegram_id)
    elif user.email:
        html = _render_email(
            "payment_success.html",
            amount=int(payment.amount),
            paid_at=paid_at_str,
            email=user.email,
            cabinet_url=cabinet_url,
            course_title=course_title,
            tariff_name=tariff_name,
            tariff_slug=tariff_slug,
            chat_invite_url=chat_invite_url,
        )
        enqueue_email(
            user.email,
            f"Оплата прошла успешно — доступ к «{course_title}» открыт",
            html,
        )
        logger.info("Payment success email queued for %s", user.email)

    # VIP: notify admin (manual personal chat)
    if tariff_slug == "vip":
        admin_html = _render_email(
            "admin_vip_purchase.html",
            user_id=user.id,
            email=user.email or "—",
            telegram_id=user.telegram_id or "—",
            username=user.username or user.name or "—",
            amount=int(payment.amount),
            paid_at=paid_at_str,
            course_title=course_title,
        )
        if settings.ADMIN_NOTIFY_EMAIL:
            enqueue_email(
                settings.ADMIN_NOTIFY_EMAIL,
                f"VIP покупка: user #{user.id}",
                admin_html,
            )
        if settings.BOT_ENABLED and settings.ADMIN_TELEGRAM_ID:
            try:
                await bot.send_message(
                    chat_id=settings.ADMIN_TELEGRAM_ID,
                    text=(
                        f"⭐ VIP покупка\n"
                        f"User #{user.id}\n"
                        f"Email: {user.email or '—'}\n"
                        f"TG: {user.telegram_id or '—'}\n"
                        f"Курс: {course_title}\n"
                        f"Сумма: {int(payment.amount)} ₽"
                    ),
                )
            except Exception as e:
                logger.error("Failed to notify admin via Telegram: %s", e)


@router.post("/yookassa", dependencies=[Depends(verify_yookassa_source)])
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
        logger.info(
            "Received webhook: %s", json.dumps(body, indent=2, ensure_ascii=False)
        )

        if body.get("event") != "payment.succeeded":
            return {"status": "ok", "message": "Event ignored"}

        payment_id = body.get("object", {}).get("id")
        if not payment_id:
            logger.error("No payment_id in webhook")
            raise HTTPException(status_code=400, detail="No payment_id")

        query = select(Payment).where(Payment.payment_id == payment_id)
        result = await db.execute(query)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.error("Payment not found in DB, ignoring webhook: %s", payment_id)
            return {"status": "ok", "message": "Unknown payment ignored"}

        if payment.status == "succeeded":
            logger.info("Payment %s already processed", payment_id)
            return {"status": "ok", "message": "Already processed"}

        remote = await _fetch_remote_payment(payment_id)
        if remote is None:
            raise HTTPException(
                status_code=502, detail="Cannot verify payment with YooKassa"
            )

        if getattr(remote, "status", None) != "succeeded":
            logger.warning(
                "Payment %s is not succeeded in YooKassa (status=%s)",
                payment_id,
                getattr(remote, "status", None),
            )
            return {"status": "ok", "message": "Payment not succeeded"}

        remote_amount = float(remote.amount.value)
        if abs(remote_amount - float(payment.amount)) > AMOUNT_TOLERANCE:
            logger.error(
                "Amount mismatch for payment %s: DB=%s, YooKassa=%s",
                payment_id,
                payment.amount,
                remote_amount,
            )
            raise HTTPException(status_code=400, detail="Amount mismatch")

        # Enrich from YooKassa metadata if DB row missing course/tariff
        meta = getattr(remote, "metadata", None) or {}
        if isinstance(meta, dict):
            if not payment.course_id and meta.get("course_id"):
                payment.course_id = int(meta["course_id"])
            if not payment.tariff_slug and meta.get("tariff_slug"):
                payment.tariff_slug = meta["tariff_slug"]
            if not payment.tariff_id and meta.get("tariff_id"):
                payment.tariff_id = int(meta["tariff_id"])

        payment.status = "succeeded"
        payment.paid_at = datetime.utcnow()

        user_query = select(User).where(User.id == payment.user_id)
        user_result = await db.execute(user_query)
        user = user_result.scalar_one_or_none()

        if not user:
            logger.error("User not found: %s", payment.user_id)
            raise HTTPException(status_code=404, detail="User not found")

        course = None
        if payment.course_id:
            c_result = await db.execute(
                select(Course).where(Course.id == payment.course_id)
            )
            course = c_result.scalar_one_or_none()
        if course is None:
            course = await get_primary_course(db)
            if course and not payment.course_id:
                payment.course_id = course.id

        tariff_slug = payment.tariff_slug or "pro"
        if payment.course_id:
            await grant_course_access(db, user, payment.course_id, tariff_slug)
        else:
            user.has_access = True
            user.access_granted_at = datetime.utcnow()

        await db.commit()
        logger.info(
            "Access granted to user %s for payment %s (course=%s tariff=%s)",
            user.id,
            payment_id,
            payment.course_id,
            tariff_slug,
        )

        try:
            await _notify_purchase(user, payment, course, tariff_slug, db)
        except Exception as e:
            logger.error("Failed to send payment notification: %s", e)

        return {"status": "ok", "message": "Payment processed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error processing webhook: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proonline")
async def proonline_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Входящий webhook ProOnline: payment.status_changed.
    Доступ выдаём на status=paid (идемпотентно по event_id).
    transferred — только догоняем доступ, если paid пропустили.
    """
    raw = await request.body()
    header_map = {k: v for k, v in request.headers.items()}
    secret = (settings.PROONLINE_WEBHOOK_SECRET or "").strip()
    if not secret:
        logger.error("ProOnline webhook rejected: PROONLINE_WEBHOOK_SECRET empty")
        raise HTTPException(status_code=503, detail="Webhook not configured")

    if not verify_webhook_signature(body=raw, headers=header_map, secret=secret):
        logger.warning("ProOnline webhook: invalid signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event")
    event_id = body.get("event_id")
    status = body.get("status")
    external_order_id = body.get("external_order_id")

    logger.info(
        "ProOnline webhook: event=%s status=%s order=%s event_id=%s",
        event,
        status,
        external_order_id,
        event_id,
    )

    if event != "payment.status_changed":
        return {"status": "ok", "message": "Event ignored"}

    if not event_id or not external_order_id:
        raise HTTPException(status_code=400, detail="Missing event_id or external_order_id")

    # Идемпотентность: одно event_id — один раз
    existing_event = await db.execute(
        select(WebhookEvent).where(
            WebhookEvent.provider == "proonline",
            WebhookEvent.event_id == event_id,
        )
    )
    if existing_event.scalar_one_or_none():
        logger.info("ProOnline event %s already processed", event_id)
        return {"status": "ok", "message": "Already processed"}

    db.add(
        WebhookEvent(
            provider="proonline",
            event_id=event_id,
            payload=raw.decode("utf-8", errors="replace")[:8000],
        )
    )

    payment_result = await db.execute(
        select(Payment).where(Payment.payment_id == external_order_id)
    )
    payment = payment_result.scalar_one_or_none()
    if not payment:
        logger.error("ProOnline order not found: %s", external_order_id)
        await db.commit()
        return {"status": "ok", "message": "Unknown order ignored"}

    if status == "failed":
        if payment.status == "pending":
            payment.status = "cancelled"
            await db.commit()
        else:
            await db.commit()
        return {"status": "ok", "message": "Payment failed recorded"}

    if status == "refunded":
        # Не отзываем доступ автоматически — только фиксируем статус платежа.
        if payment.status == "succeeded":
            payment.status = "refunded"
        await db.commit()
        return {"status": "ok", "message": "Refund recorded"}

    if status not in ("paid", "transferred"):
        await db.commit()
        return {"status": "ok", "message": "Status ignored"}

    # transferred без предшествующего paid — тоже открываем доступ один раз
    if payment.status == "succeeded":
        await db.commit()
        return {"status": "ok", "message": "Already succeeded"}

    user_result = await db.execute(select(User).where(User.id == payment.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        logger.error("ProOnline user missing for payment %s", payment.id)
        await db.commit()
        raise HTTPException(status_code=404, detail="User not found")

    # Сверяем сумму, если CRM прислала amount
    remote_amount = body.get("amount")
    if remote_amount is not None:
        try:
            if abs(float(remote_amount) - float(payment.amount)) > AMOUNT_TOLERANCE:
                logger.error(
                    "ProOnline amount mismatch order=%s db=%s remote=%s",
                    external_order_id,
                    payment.amount,
                    remote_amount,
                )
                await db.rollback()
                raise HTTPException(status_code=400, detail="Amount mismatch")
        except (TypeError, ValueError):
            pass

    payment.status = "succeeded"
    payment.paid_at = datetime.utcnow()
    payment.provider = payment.provider or "proonline"

    course = None
    if payment.course_id:
        c_result = await db.execute(select(Course).where(Course.id == payment.course_id))
        course = c_result.scalar_one_or_none()
    if course is None:
        course = await get_primary_course(db)
        if course and not payment.course_id:
            payment.course_id = course.id

    tariff_slug = payment.tariff_slug or "pro"
    if payment.course_id:
        await grant_course_access(db, user, payment.course_id, tariff_slug)
    else:
        user.has_access = True
        user.access_granted_at = datetime.utcnow()

    await db.commit()
    logger.info(
        "ProOnline access granted user=%s order=%s tariff=%s status=%s",
        user.id,
        external_order_id,
        tariff_slug,
        status,
    )

    try:
        await _notify_purchase(user, payment, course, tariff_slug, db)
    except Exception as e:
        logger.error("ProOnline notify failed: %s", e)

    return {"status": "ok", "message": "Payment processed"}


@router.post("/test-payment")
async def test_payment_webhook(db: AsyncSession = Depends(get_db)):
    if not settings.DEBUG:
        raise HTTPException(status_code=403, detail="Only available in debug mode")

    query = select(User).where(User.telegram_id == 123456789)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=123456789,
            username="test_user",
            first_name="Test",
            has_access=False,
        )
        db.add(user)
        await db.flush()

    course = await get_primary_course(db)
    payment = Payment(
        user_id=user.id,
        amount=9990,
        payment_id=f"test_{datetime.utcnow().timestamp()}",
        status="pending",
        course_id=course.id if course else None,
        tariff_slug="pro",
    )
    db.add(payment)
    await db.flush()

    if course:
        await grant_course_access(db, user, course.id, "pro")
    else:
        user.has_access = True
        user.access_granted_at = datetime.utcnow()

    payment.status = "succeeded"
    payment.paid_at = datetime.utcnow()
    await db.commit()

    return {
        "status": "ok",
        "user_id": user.id,
        "payment_id": payment.payment_id,
        "message": "Test payment processed",
    }
