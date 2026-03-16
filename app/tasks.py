from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import logging
import aiohttp
import asyncio
from datetime import datetime, timedelta
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.payment import Payment
from app.config import settings

logger = logging.getLogger(__name__)


@shared_task
def send_receipt_to_email(payment_id: int, user_email: str, amount: float):
    """
    Отправляет чек на email пользователя (фоновая задача)
    """
    try:
        # Создаем письмо
        msg = MIMEMultipart()
        msg['From'] = settings.SMTP_FROM
        msg['To'] = user_email
        msg['Subject'] = "Чек об оплате курса"

        body = f"""
        Благодарим за покупку!

        Сумма: {amount}₽
        Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}

        Чек отправлен в ФНС автоматически.
        С уважением, команда курса.
        """

        msg.attach(MIMEText(body, 'plain'))

        # Отправляем
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        logger.info(f"Receipt sent to {user_email}")

    except Exception as e:
        logger.error(f"Failed to send receipt: {e}")


@shared_task
def cleanup_expired_links():
    """
    Очищает устаревшие записи о ссылках (если храним их в БД)
    """
    # Эта задача зависит от того, храним ли мы ссылки в БД
    # Если используем Kinescope, они сами управляют сроком жизни
    pass


@shared_task
def send_daily_report():
    """
    Отправляет ежедневный отчет админу о продажах
    """

    async def _get_stats():
        async with AsyncSessionLocal() as db:
            # Продажи за сегодня
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)

            payments_today = await db.execute(
                select(Payment)
                .where(
                    and_(
                        Payment.paid_at >= today,
                        Payment.paid_at < tomorrow,
                        Payment.status == "succeeded"
                    )
                )
            )
            payments = payments_today.scalars().all()

            total_amount = sum(p.amount for p in payments)
            new_users = await db.execute(
                select(User)
                .where(User.created_at >= today)
            )
            new_users = new_users.scalars().all()

            return {
                "total_sales": len(payments),
                "total_amount": total_amount,
                "new_users": len(new_users),
                "payments": payments
            }

    # Запускаем асинхронную функцию в синхронном контексте
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stats = loop.run_until_complete(_get_stats())
    loop.close()

    # Отправляем отчет (telegram, email и т.д.)
    logger.info(f"Daily report: {stats}")


@shared_task
def monitor_piracy():
    """
    Мониторит пиратские сайты и Telegram каналы на наличие курса
    """

    async def _search_telegram():
        # Поиск в Telegram (через клиент или публичные API)
        # Это сложная тема, требует отдельной реализации
        pass

    async def _search_youtube():
        # Поиск на YouTube
        async with aiohttp.ClientSession() as session:
            # Здесь нужно использовать YouTube Data API
            pass

    async def _search_google():
        # Поиск в Google
        keywords = [
            f'"{settings.COURSE_NAME}" скачать',
            f'"{settings.COURSE_NAME}" слив',
            f'"{settings.COURSE_NAME}" торрент'
        ]

        async with aiohttp.ClientSession() as session:
            for keyword in keywords:
                url = f"https://www.googleapis.com/customsearch/v1"
                params = {
                    "key": settings.GOOGLE_API_KEY,
                    "cx": settings.GOOGLE_CX,
                    "q": keyword
                }

                try:
                    async with session.get(url, params=params) as resp:
                        data = await resp.json()
                        # Анализируем результаты
                        if data.get("items"):
                            logger.warning(f"Found potential piracy: {keyword}")
                            # Здесь можно сохранять результаты или отправлять уведомление
                except Exception as e:
                    logger.error(f"Search error: {e}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_search_google())
    loop.close()


@shared_task
def backup_database():
    """
    Создает бэкап базы данных
    """
    import subprocess
    import os
    from datetime import datetime

    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)

    filename = f"{backup_dir}/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"

    # Команда для PostgreSQL
    cmd = f"pg_dump {settings.DATABASE_URL} > {filename}"

    try:
        subprocess.run(cmd, shell=True, check=True)
        logger.info(f"Database backup created: {filename}")

        # Загружаем в облачное хранилище (S3, Яндекс.Облако и т.д.)
        # upload_to_cloud(filename)

    except subprocess.CalledProcessError as e:
        logger.error(f"Backup failed: {e}")


# Задача для отправки массовых уведомлений
@shared_task
def send_bulk_notification(user_ids: list, message: str):
    """
    Отправляет уведомление группе пользователей
    """
    from app.bot.bot import bot
    import asyncio

    async def _send():
        success = 0
        failed = 0

        for user_id in user_ids:
            try:
                await bot.send_message(user_id, message)
                success += 1
                await asyncio.sleep(0.05)  # Чтобы не спамить
            except Exception:
                failed += 1

        logger.info(f"Bulk notification sent: {success} success, {failed} failed")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_send())
    loop.close()