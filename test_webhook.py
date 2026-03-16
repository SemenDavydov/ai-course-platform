# test_webhook.py
import asyncio
import httpx
import sys
import os

# Добавляем путь к проекту для импорта config (опционально)
sys.path.append(os.path.dirname(__file__))


async def send_test_webhook():
    """Имитирует вебхук от ЮKassa о успешном платеже"""

    # Сначала проверим, доступен ли сервер
    try:
        async with httpx.AsyncClient() as client:
            health_check = await client.get("http://localhost:8000/")
            print(f"✅ FastAPI сервер доступен: {health_check.status_code}")
    except Exception as e:
        print(f"❌ FastAPI сервер НЕ доступен: {e}")
        print("   Убедись, что uvicorn запущен в отдельном терминале")
        return

    webhook_data = {
        "event": "payment.succeeded",
        "object": {
            "id": "31464319-000f-5001-9000-16803d176f91",  # ID из твоего платежа
            "status": "succeeded",
            "amount": {
                "value": "3990.00",
                "currency": "RUB"
            },
            "description": "Оплата курса Экспресс курс по созданию ИИ анимаций и изображений",
            "metadata": {
                "user_id": 1  # Проверь в БД правильный ID
            }
        }
    }

    print(f"📤 Отправляем вебхук на http://localhost:8000/webhooks/yookassa")
    print(f"📦 Данные: {webhook_data}")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://localhost:8000/webhooks/yookassa",
                json=webhook_data,
                timeout=10.0
            )
            print(f"📥 Статус ответа: {response.status_code}")
            print(f"📥 Тело ответа: {response.text}")

            if response.status_code == 200:
                print("✅ Вебхук успешно обработан!")
            else:
                print("❌ Ошибка при обработке вебхука")

        except Exception as e:
            print(f"❌ Ошибка при отправке: {e}")


if __name__ == "__main__":
    asyncio.run(send_test_webhook())