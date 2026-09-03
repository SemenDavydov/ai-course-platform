from yookassa import Configuration, Payment as YooPayment
import uuid
from typing import Optional, Dict
from datetime import datetime

from app.config import settings
from app.models.user import User


class PaymentService:
    """Сервис для работы с ЮKassa"""

    def __init__(self):
        Configuration.account_id = settings.YOOKASSA_SHOP_ID
        Configuration.secret_key = settings.YOOKASSA_SECRET_KEY

    async def create_payment(
        self,
        user: User,
        amount: float,
        description: str,
        course_id: int = None,
        return_url: str = None,
        tariff_slug: str = None,
        tariff_id: int = None,
    ) -> Optional[Dict]:
        if return_url is None:
            return_url = f"https://t.me/{settings.BOT_USERNAME}"

        idempotence_key = str(uuid.uuid4())

        receipt_data = {
            "customer": {
                "email": user.email if user.email else f"user_{user.id}@example.com"
            },
            "items": [
                {
                    "description": description[:128],
                    "quantity": 1.0,
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": "RUB",
                    },
                    "vat_code": 1,
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }
            ],
        }

        metadata = {
            "user_id": user.id,
            "course_id": course_id,
            "telegram_id": user.telegram_id,
        }
        if tariff_slug:
            metadata["tariff_slug"] = tariff_slug
        if tariff_id:
            metadata["tariff_id"] = tariff_id

        try:
            payment = YooPayment.create(
                {
                    "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                    "confirmation": {"type": "redirect", "return_url": return_url},
                    "capture": True,
                    "description": description[:128],
                    "metadata": metadata,
                    "receipt": receipt_data,
                },
                idempotence_key,
            )

            return {
                "payment_id": payment.id,
                "confirmation_url": payment.confirmation.confirmation_url,
                "status": payment.status,
            }
        except Exception as e:
            print(f"Error creating payment: {e}")
            return None

    async def process_successful_payment(self, payment_data: dict, db):
        from app.models.payment import Payment
        from app.models.user import User
        from app.services.access import grant_course_access
        from sqlalchemy import select

        payment_id = payment_data.get("id")
        metadata = payment_data.get("metadata", {}) or {}
        user_id = metadata.get("user_id")

        if not user_id:
            raise ValueError("No user_id in payment metadata")

        query = select(Payment).where(Payment.payment_id == payment_id)
        result = await db.execute(query)
        payment = result.scalar_one_or_none()

        course_id = metadata.get("course_id")
        tariff_slug = metadata.get("tariff_slug") or "pro"
        tariff_id = metadata.get("tariff_id")

        if not payment:
            payment = Payment(
                user_id=int(user_id),
                amount=float(payment_data.get("amount", {}).get("value", 0)),
                payment_id=payment_id,
                status="succeeded",
                description=payment_data.get("description", "Оплата курса"),
                paid_at=datetime.utcnow(),
                course_id=int(course_id) if course_id else None,
                tariff_id=int(tariff_id) if tariff_id else None,
                tariff_slug=tariff_slug,
            )
            db.add(payment)
        else:
            payment.status = "succeeded"
            payment.paid_at = datetime.utcnow()
            if course_id and not payment.course_id:
                payment.course_id = int(course_id)
            if tariff_slug:
                payment.tariff_slug = tariff_slug

        user_query = select(User).where(User.id == int(user_id))
        user_result = await db.execute(user_query)
        user = user_result.scalar_one()

        if payment.course_id:
            await grant_course_access(
                db, user, payment.course_id, payment.tariff_slug or tariff_slug
            )
        else:
            user.has_access = True
            user.access_granted_at = datetime.utcnow()

        await db.commit()

        return {
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "payment_id": payment_id,
        }
