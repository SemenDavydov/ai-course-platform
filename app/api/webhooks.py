from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
import logging
from datetime import datetime
import json

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


@router.post("/yookassa")
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Обрабатывает уведомления от ЮKassa о статусе платежей
    Документация: https://yookassa.ru/developers/using-api/webhooks
    """

    try:
        # Получаем тело запроса
        body = await request.json()
        logger.info(f"Received webhook: {json.dumps(body, indent=2, ensure_ascii=False)}")

        # Проверяем тип события
        if body.get("event") != "payment.succeeded":
            return {"status": "ok", "message": "Event ignored"}

        # Получаем объект платежа
        payment_object = body.get("object", {})
        payment_id = payment_object.get("id")

        if not payment_id:
            logger.error("No payment_id in webhook")
            raise HTTPException(status_code=400, detail="No payment_id")

        # Ищем платеж в нашей БД
        query = select(Payment).where(Payment.payment_id == payment_id)
        result = await db.execute(query)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.error(f"Payment not found in DB: {payment_id}")

            # Если платежа нет в БД, возможно, это тестовый или платёж создан не через нашу систему
            # Но мы можем создать запись о платеже на основе данных из вебхука
            logger.info("Creating payment record from webhook data")

            # Извлекаем метаданные, которые мы передавали при создании платежа
            metadata = payment_object.get("metadata", {})
            user_id = metadata.get("user_id")

            if not user_id:
                logger.error("No user_id in metadata")
                raise HTTPException(status_code=400, detail="No user_id in metadata")

            # Создаем запись о платеже
            payment = Payment(
                user_id=int(user_id),
                amount=float(payment_object.get("amount", {}).get("value", 0)),
                payment_id=payment_id,
                status="succeeded",
                description=payment_object.get("description", "Оплата курса"),
                paid_at=datetime.utcnow()
            )
            db.add(payment)
            await db.flush()

        # Если платеж уже обработан
        if payment.status == "succeeded":
            logger.info(f"Payment {payment_id} already processed")
            return {"status": "ok", "message": "Already processed"}

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

        logger.info(f"Access granted to user {user.telegram_id} for payment {payment_id}")

        # Отправляем уведомление пользователю в Telegram
        try:
            # Генерируем приветственное сообщение
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

            # Создаем клавиатуру со ссылкой на курс
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
                reply_markup=keyboard
            )

            logger.info(f"Notification sent to user {user.telegram_id}")

        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            # Не блокируем основной процесс из-за ошибки отправки

        return {"status": "ok", "message": "Payment processed"}

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
