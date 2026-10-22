"""
Ежедневная обработка срочных доступов (тариф «Самостоятельный»).

1. Напоминание за EXPIRY_REMINDER_DAYS дней до конца: письмо (или Telegram,
   если email нет). expiry_reminder_sent_at ставится только после успешной
   отправки — повторный запуск не дублирует, неудачная отправка повторится завтра.
2. Пересчёт user.has_access у тех, чей доступ уже истёк.

Запуск (systemd timer deploy/access-expiry.timer, раз в сутки):
    venv/bin/python -m app.scripts.access_expiry
Celery на проде нет — поэтому отдельный процесс, а не beat-задача.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.course import UserCourseAccess
from app.models.user import User
from app.services.access import refresh_user_has_access
from app.services.email import send_email
from app.services.tariffs import EXPIRY_REMINDER_DAYS, SELF

logger = logging.getLogger("access_expiry")

_jinja = Environment(
    loader=FileSystemLoader("app/templates/emails"),
    autoescape=select_autoescape(["html"]),
)


@dataclass
class ExpiryResult:
    reminders_sent: int = 0
    reminders_failed: int = 0
    access_revoked: int = 0


async def _send_telegram_reminder(user: User, course_title: str, until: str) -> bool:
    if not (settings.BOT_ENABLED and user.telegram_id):
        return False
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from app.bot.bot import bot

    text = (
        f"⏳ Доступ к курсу «{course_title}» по тарифу «Самостоятельный» "
        f"закончится <b>{until}</b>.\n\n"
        "Успейте досмотреть уроки — или перейдите на Pro/VIP: поддержка куратора "
        "и доступ без ограничения двумя месяцами."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬆️ Улучшить тариф", callback_data="buy")]]
    )
    try:
        await bot.send_message(chat_id=user.telegram_id, text=text, reply_markup=keyboard)
        return True
    except Exception as e:
        logger.error("Telegram reminder failed for user %s: %s", user.id, e)
        return False


async def _send_reminder(access: UserCourseAccess) -> bool:
    user = access.user
    course_title = access.course.title if access.course else "курс"
    until = access.expires_at.strftime("%d.%m.%Y")
    site = (settings.SITE_URL or "").rstrip("/")

    if user.email:
        html = _jinja.get_template("access_expiring.html").render(
            course_title=course_title,
            access_until=until,
            upgrade_url=f"{site}/#tariffs",
            lessons_url=f"{site}/cabinet/lessons",
        )
        # smtplib синхронный — в потоке, чтобы не блокировать цикл.
        return await asyncio.to_thread(
            send_email,
            user.email,
            f"Доступ к «{course_title}» закончится {until}",
            html,
        )
    return await _send_telegram_reminder(user, course_title, until)


async def process_access_expiry(
    db: AsyncSession, *, now: datetime | None = None
) -> ExpiryResult:
    now = now or datetime.now(timezone.utc)
    result = ExpiryResult()

    # 1. Напоминания: срок наступает в ближайшие N дней, ещё не напоминали.
    due = await db.execute(
        select(UserCourseAccess)
        .where(
            UserCourseAccess.tariff_slug == SELF,
            UserCourseAccess.expires_at.is_not(None),
            UserCourseAccess.expires_at > now,
            UserCourseAccess.expires_at <= now + timedelta(days=EXPIRY_REMINDER_DAYS),
            UserCourseAccess.expiry_reminder_sent_at.is_(None),
        )
        .options(selectinload(UserCourseAccess.user), selectinload(UserCourseAccess.course))
    )
    for access in due.scalars().all():
        if access.user is None or access.user.is_blocked:
            continue
        if await _send_reminder(access):
            access.expiry_reminder_sent_at = now
            result.reminders_sent += 1
        else:
            result.reminders_failed += 1
        await db.commit()

    # 2. Истёкшие доступы: снимаем user.has_access, если больше ничего не действует.
    expired_users = await db.execute(
        select(User)
        .join(UserCourseAccess, UserCourseAccess.user_id == User.id)
        .where(
            User.has_access == True,  # noqa: E712
            UserCourseAccess.expires_at.is_not(None),
            UserCourseAccess.expires_at <= now,
        )
        .distinct()
    )
    for user in expired_users.scalars().all():
        if not await refresh_user_has_access(db, user, now=now):
            result.access_revoked += 1
    await db.commit()

    return result


async def main() -> ExpiryResult:
    async with AsyncSessionLocal() as db:
        result = await process_access_expiry(db)
    logger.info(
        "access_expiry done: reminders_sent=%s reminders_failed=%s has_access_revoked=%s",
        result.reminders_sent,
        result.reminders_failed,
        result.access_revoked,
    )
    if settings.BOT_ENABLED:
        try:
            from app.bot.bot import bot

            await bot.session.close()
        except Exception:
            pass
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
