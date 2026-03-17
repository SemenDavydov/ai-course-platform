from yookassa import Configuration, Payment as YooPayment
from yookassa.domain.notification import WebhookNotificationEventType
import uuid
from typing import Optional, Dict
from datetime import datetime

from app.config import settings
from app.models.user import User

class PaymentService:
    """Сервис для работы с ЮKassa (по документации)"""
    
    def __init__(self):
        # Настройка конфигурации строго по документации
        Configuration.account_id = settings.YOOKASSA_SHOP_ID
        Configuration.secret_key = settings.YOOKASSA_SECRET_KEY
    
    async def create_payment(
        self, 
        user: User, 
        amount: float, 
        description: str,
        course_id: int = None,
        return_url: str = None
    ) -> Optional[Dict]:
        """
        Создает платеж в ЮKassa и возвращает ссылку на оплату
        Полностью соответствует документации ЮKassa
        """
        if return_url is None:
            return_url = f"https://t.me/{settings.BOT_USERNAME}"
        
        # Генерируем уникальный ключ идемпотентности (V4 UUID как в документации)
        idempotence_key = str(uuid.uuid4())
        
        # Данные для чека (обязательно для самозанятых)
        receipt_data = {
            "customer": {
                "email": user.email if user.email else f"user_{user.id}@example.com"
            },
            "items": [
                {
                    "description": description,
                    "quantity": 1.0,
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB"
                    },
                    "vat_code": 1,  # Без НДС (для самозанятых)
                    "payment_subject": "service",
                    "payment_mode": "full_payment"
                }
            ]
        }
        
        try:
            # Создаем платеж строго по документации
            payment = YooPayment.create({
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "user_id": user.id,
                    "course_id": course_id,
                    "telegram_id": user.telegram_id
                },
                "receipt": receipt_data
            }, idempotence_key)  # Передаем ключ идемпотентности отдельно
            
            return {
                "payment_id": payment.id,
                "confirmation_url": payment.confirmation.confirmation_url,
                "status": payment.status
            }
            
        except Exception as e:
            print(f"Error creating payment: {e}")
            return None
    
    async def process_successful_payment(self, payment_data: dict, db):
        """
        Обрабатывает успешный платеж
        Вызывается из вебхука
        """
        from app.models.payment import Payment
        from app.models.user import User
        from sqlalchemy import select
        
        payment_id = payment_data.get("id")
        metadata = payment_data.get("metadata", {})
        user_id = metadata.get("user_id")
        
        if not user_id:
            raise ValueError("No user_id in payment metadata")
        
        # Ищем платеж в БД
        query = select(Payment).where(Payment.payment_id == payment_id)
        result = await db.execute(query)
        payment = result.scalar_one_or_none()
        
        if not payment:
            # Создаем новый платеж, если не найден
            payment = Payment(
                user_id=int(user_id),
                amount=float(payment_data.get("amount", {}).get("value", 0)),
                payment_id=payment_id,
                status="succeeded",
                description=payment_data.get("description", "Оплата курса"),
                paid_at=datetime.utcnow()
            )
            db.add(payment)
        else:
            # Обновляем существующий
            payment.status = "succeeded"
            payment.paid_at = datetime.utcnow()
        
        # Выдаем доступ пользователю
        user_query = select(User).where(User.id == int(user_id))
        user_result = await db.execute(user_query)
        user = user_result.scalar_one()
        user.has_access = True
        user.access_granted_at = datetime.utcnow()
        
        await db.commit()
        
        return {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "payment_id": payment_id
        }
