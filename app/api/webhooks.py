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
from app.services.payment import PaymentService
from app.services.video import VideoService
from app.bot.bot import bot
from app.config import settings

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Сети, с которых ЮKassa шлёт уведомления.
# https://yookassa.ru/developers/using-api/webhooks#ip
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

# Расхождение суммы в пределах копейки считаем округлением, а не подменой.
AMOUNT_TOLERANCE = 0.01


async def verify_yookassa_source(request: Request) -> None:
    """
    Пропускает только запросы с IP-адресов ЮKassa.

    Требует, чтобы uvicorn запускался с --proxy-headers --forwarded-allow-ips=127.0.0.1:
    иначе за nginx сюда придёт 127.0.0.1 и все уведомления будут отклонены.
    """
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
    """
    Забирает платёж из API ЮKassa. SDK синхронный — уводим в тред,
    чтобы не блокировать event loop. None, если запрос не удался.
    """
    # PaymentService в __init__ проставляет Configuration.account_id/secret_key
    PaymentService()
    try:
        return await asyncio.to_thread(YooPayment.find_one, payment_id)
    except Exception as e:
        logger.error("Failed to fetch payment %s from YooKassa: %s", payment_id, e)
        return None


@router.post("/yookassa", dependencies=[Depends(verify_yookassa_source)])
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Обрабатывает уведомления от ЮKassa о статусе платежей.

    Телу запроса не доверяем: оно лишь указывает, какой платёж перепроверить.
    Факт и сумма оплаты подтверждаются запросом к API ЮKassa, а сам платёж
    должен быть заранее создан нами в /api/v1/payments/create.
    Документация: https://yookassa.ru/developers/using-api/webhooks
    """

    try:
        body = await request.json()
        logger.info(f"Received webhook: {json.dumps(body, indent=2, ensure_ascii=False)}")

        if body.get("event") != "payment.succeeded":
            return {"status": "ok", "message": "Event ignored"}

        payment_id = body.get("object", {}).get("id")
        if not payment_id:
            logger.error("No payment_id in webhook")
            raise HTTPException(status_code=400, detail="No payment_id")

        # Платёж обязан существовать в нашей БД: его создаёт /api/v1/payments/create.
        # Записи «на лету» из тела запроса не создаём — иначе доступ можно выпросить
        # произвольным POST-ом.
        query = select(Payment).where(Payment.payment_id == payment_id)
        result = await db.execute(query)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.error(f"Payment not found in DB, ignoring webhook: {payment_id}")
            return {"status": "ok", "message": "Unknown payment ignored"}

        # Если платеж уже обработан — не дублируем
        if payment.status == "succeeded":
            logger.info(f"Payment {payment_id} already processed")
            return {"status": "ok", "message": "Already processed"}

        # Подтверждаем оплату у ЮKassa, а не по телу запроса
        remote = await _fetch_remote_payment(payment_id)
        if remote is None:
            raise HTTPException(status_code=502, detail="Cannot verify payment with YooKassa")

        if getattr(remote, "status", None) != "succeeded":
            logger.warning(
                "Payment %s is not succeeded in YooKassa (status=%s), access not granted",
                payment_id, getattr(remote, "status", None),
            )
            return {"status": "ok", "message": "Payment not succeeded"}

        remote_amount = float(remote.amount.value)
        if abs(remote_amount - float(payment.amount)) > AMOUNT_TOLERANCE:
            logger.error(
                "Amount mismatch for payment %s: DB=%s, YooKassa=%s",
                payment_id, payment.amount, remote_amount,
            )
            raise HTTPException(status_code=400, detail="Amount mismatch")

        # Обновляем статус платежа
        payment.status = "succeeded"
        payment.paid_at = datetime.utcnow()

        # Находим пользователя
        user_query = select(User).where(User.id == payment.user_id)
        user_result = await db.execute(user_query)
        user = user_result.scalar_one_or_none()

        if not user:
            logger.error(f"User not found: {payment.user_id}")
            raise HTTPException(status_code=404, detail="User not found")

        # Выдаем пользователю доступ к курсу
        user.has_access = True
        user.access_granted_at = datetime.utcnow()

        # Сохраняем изменения
        await db.commit()

        logger.info(f"Access granted to user {user.id} for payment {payment_id}")

        # Уведомление: Telegram если бот включён и есть telegram_id, иначе email
        try:
            use_telegram = (
                settings.BOT_ENABLED
                and user.telegram_id is not None
                and getattr(user, "registration_source", None) != "web"
            )

            if use_telegram:
                welcome_text = (
                    "🎉 *Поздравляю с покупкой!*\n\n"
                    "✅ Доступ к курсу полностью открыт и будет действовать бессрочно.\n\n"
                    "🔐 *Важно:* Все видео защищены персональными водяными знаками с вашими данными.\n"
                    "Пожалуйста, не передавайте доступ третьим лицам — это может привести к блокировке.\n\n"
                    "📚 *Как начать обучение:*\n"
                    "1. Нажмите кнопку ниже «📖 Перейти к курсу»\n"
                    "2. Вы попадете в личный кабинет, где собраны все уроки\n"
                    "3. Каждое видео открывается по защищенной ссылке\n\n"
                    "💡 Если возникнут вопросы — пишите сюда, я на связи!"
                )
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="📖 Перейти к курсу",
                                              url=f"https://t.me/{settings.BOT_USERNAME}?start=course")]
                    ]
                )
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=welcome_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
                logger.info(f"Telegram notification sent to user {user.telegram_id}")

            elif user.email:
                from jinja2 import Environment, FileSystemLoader, select_autoescape
                from app.tasks import enqueue_email

                _jinja = Environment(
                    loader=FileSystemLoader("app/templates/emails"),
                    autoescape=select_autoescape(["html"]),
                )
                paid_at_str = (payment.paid_at or datetime.utcnow()).strftime("%d.%m.%Y %H:%M")
                html = _jinja.get_template("payment_success.html").render(
                    amount=int(payment.amount),
                    paid_at=paid_at_str,
                    email=user.email,
                    cabinet_url=f"{settings.SITE_URL}/",
                )
                # enqueue_email, а не .delay(): без Redis задача бы не поставилась
                # и письмо о покупке потерялось бы. Здесь при отсутствии брокера
                # письмо уходит синхронно.
                enqueue_email(user.email, "Оплата прошла успешно — доступ к курсу открыт", html)
                logger.info(f"Payment success email sent/queued for {user.email}")

        except Exception as e:
            logger.error(f"Failed to send payment notification: {e}")
            # Не блокируем основной процесс

        return {"status": "ok", "message": "Payment processed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-payment")
async def test_payment_webhook(db: AsyncSession = Depends(get_db)):
    """
    Тестовый эндпоинт для ручного создания успешного платежа (только для разработки!)
    """
    if not settings.DEBUG:
        raise HTTPException(status_code=403, detail="Only available in debug mode")

    # Создаем тестового пользователя, если его нет
    query = select(User).where(User.telegram_id == 123456789)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=123456789,
            username="test_user",
            first_name="Test",
            has_access=False
        )
        db.add(user)
        await db.flush()

    # Создаем тестовый платеж
    payment = Payment(
        user_id=user.id,
        amount=5000,
        payment_id=f"test_{datetime.utcnow().timestamp()}",
        status="pending"
    )
    db.add(payment)
    await db.commit()

    # Выдаем доступ
    user.has_access = True
    user.access_granted_at = datetime.utcnow()
    payment.status = "succeeded"
    payment.paid_at = datetime.utcnow()
    await db.commit()

    return {
        "status": "ok",
        "user_id": user.id,
        "payment_id": payment.payment_id,
        "message": "Test payment processed"
    }
