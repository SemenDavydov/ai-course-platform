from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import logging
import aiohttp
import asyncio
from datetime import datetime, timedelta
import json

from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.payment import Payment
from app.config import settings
from app.services.email import send_email

logger = logging.getLogger(__name__)


@shared_task
def send_email_task(to: str, subject: str, html_body: str) -> bool:
    """
    Celery-обёртка для асинхронной отправки произвольного HTML-письма.
    """
    return send_email(to, subject, html_body)


def enqueue_email(to: str, subject: str, html_body: str) -> bool:
    """
    Безопасная отправка email:
    - сначала пробуем поставить задачу в Celery;
    - если брокер недоступен, отправляем синхронно через SMTP.
    """
    try:
        send_email_task.delay(to, subject, html_body)
        return True
    except Exception as exc:
        logger.error("Failed to queue email task, fallback to direct send: %s", exc)
        return send_email(to, subject, html_body)


@shared_task
def send_receipt_to_email(payment_id: int, user_email: str, amount: float):
    """
    Отправляет чек об оплате на email пользователя (фоновая задача).
    """
    html_body = f"""
    <p>Благодарим за покупку!</p>
    <p>Сумма: <strong>{amount}₽</strong><br>
    Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
    <p>Чек отправлен в ФНС автоматически.<br>
    С уважением, команда курса.</p>
    """
    send_email(user_email, "Чек об оплате курса", html_body)


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


@shared_task
def notify_bot_migration():
    """
    One-off задача: рассылает письмо «Мы переехали на сайт» всем пользователям
    с has_access=True и заполненным email (для мигрированных из бота).
    Запускать вручную: celery -A app.celery_app call app.tasks.notify_bot_migration
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).where(User.has_access == True, User.email.isnot(None))
            )
            users = result.scalars().all()

        _jinja = Environment(
            loader=FileSystemLoader("app/templates/emails"),
            autoescape=select_autoescape(["html"]),
        )
        template = _jinja.get_template("set_password_migration.html")

        sent = 0
        failed = 0
        for user in users:
            try:
                html = template.render(
                    email=user.email,
                    setup_url=f"{settings.SITE_URL}/auth/password-setup/request",
                    site_url=settings.SITE_URL,
                )
                ok = send_email(user.email, "Мы переехали на сайт — установите пароль", html)
                if ok:
                    sent += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"notify_bot_migration: failed for {user.email}: {e}")
                failed += 1

        logger.info(f"notify_bot_migration: sent={sent}, failed={failed}, total={len(users)}")
        return {"sent": sent, "failed": failed, "total": len(users)}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_run())
    loop.close()
    return result


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