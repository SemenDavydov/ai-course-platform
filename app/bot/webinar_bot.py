"""
Lead-magnet / webinar Telegram bot.

Запуск:
  WEBINAR_BOT_ENABLED=true
  python -m app.bot.webinar_bot

Использует тот же TELEGRAM_API_BASE (прокси Cloudflare Worker), что и основной бот.
Рассылки идут по расписанию Europe/Moscow; повторная отправка той же кампании
блокируется записью в webinar_broadcast_log.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import select

from app.bot.httpx_session import HttpxSession
from app.bot import webinar_messages as msg
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.webinar import WebinarBroadcastLog, WebinarSubscriber

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webinar_bot")

MSK = ZoneInfo("Europe/Moscow")
LEAD_MAGNET_DELAY_SEC = 5
BROADCAST_PAUSE_SEC = 0.05  # ~20 msg/s — безопасный лимит Telegram
CATCHUP_HOURS = 6  # если бот был выключен в момент рассылки — догоняем в окне


def _parse_msk(value: str) -> datetime | None:
    """Парсит 'YYYY-MM-DDTHH:MM:SS' или ISO как время Москвы."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        logger.error("Некорректная дата расписания: %r", value)
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def _build_bot() -> Bot:
    telegram_api = (
        TelegramAPIServer.from_base(settings.TELEGRAM_API_BASE.rstrip("/"))
        if settings.TELEGRAM_API_BASE
        else PRODUCTION
    )
    return Bot(
        token=settings.WEBINAR_BOT_TOKEN,
        session=HttpxSession(api=telegram_api),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


bot = _build_bot() if settings.WEBINAR_BOT_TOKEN else None
dp = Dispatcher()


async def _upsert_subscriber(
    telegram_id: int,
    *,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> WebinarSubscriber:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WebinarSubscriber).where(WebinarSubscriber.telegram_id == telegram_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            sub = WebinarSubscriber(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
            )
            db.add(sub)
        else:
            sub.username = username
            sub.first_name = first_name
            sub.last_name = last_name
            sub.is_active = True
        await db.commit()
        await db.refresh(sub)
        return sub


async def _mark_lead_magnet_sent(telegram_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WebinarSubscriber).where(WebinarSubscriber.telegram_id == telegram_id)
        )
        sub = result.scalar_one_or_none()
        if sub:
            sub.lead_magnet_sent = True
            await db.commit()


async def _send_lead_magnet_later(chat_id: int) -> None:
    await asyncio.sleep(LEAD_MAGNET_DELAY_SEC)
    if bot is None:
        return
    try:
        await bot.send_message(chat_id, msg.lead_magnet_message(), disable_web_page_preview=False)
        await _mark_lead_magnet_sent(chat_id)
    except Exception:
        logger.exception("Не удалось отправить лид-магнит chat_id=%s", chat_id)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        return
    await _upsert_subscriber(
        user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )
    await message.answer(msg.welcome_message())
    asyncio.create_task(_send_lead_magnet_later(user.id))


@dp.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


def _is_admin(telegram_id: int) -> bool:
    raw = (settings.WEBINAR_ADMIN_TELEGRAM_IDS or "").strip()
    if not raw:
        return False
    allowed = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
    return telegram_id in allowed


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not message.from_user or not _is_admin(message.from_user.id):
        return
    async with AsyncSessionLocal() as db:
        total = (
            await db.execute(select(WebinarSubscriber).where(WebinarSubscriber.is_active == True))
        ).scalars().all()
        logs = (await db.execute(select(WebinarBroadcastLog))).scalars().all()
    lines = [f"Активных подписчиков: <b>{len(total)}</b>"]
    for log in logs:
        lines.append(
            f"• {log.campaign_key}: ok={log.recipients_ok} fail={log.recipients_fail} "
            f"at={log.started_at}"
        )
    await message.answer("\n".join(lines) or "Пока пусто.")


@dp.message(Command("send_now"))
async def cmd_send_now(message: Message) -> None:
    """Админ: /send_now announce|remind|last_push — принудительная рассылка."""
    if not message.from_user or not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or parts[1].strip() not in CAMPAIGNS:
        await message.answer(
            "Использование: <code>/send_now announce|remind|last_push</code>"
        )
        return
    key = parts[1].strip()
    await message.answer(f"Запускаю рассылку <b>{key}</b>…")
    ok, fail, total = await run_broadcast(key, force=True)
    await message.answer(f"Готово: всего {total}, ок {ok}, ошибок {fail}.")


async def _campaign_already_sent(campaign_key: str) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WebinarBroadcastLog).where(WebinarBroadcastLog.campaign_key == campaign_key)
        )
        return result.scalar_one_or_none() is not None


async def _list_active_chat_ids() -> list[int]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(WebinarSubscriber.telegram_id).where(WebinarSubscriber.is_active == True)
            )
        ).scalars().all()
    return list(rows)


def _campaign_text(key: str) -> str:
    chat = (settings.WEBINAR_CHAT_INVITE_URL or "").strip()
    webinar = (settings.WEBINAR_TELEMOST_URL or "").strip()
    if key == "announce":
        return msg.announce_message(chat)
    if key == "remind":
        return msg.remind_message(chat or webinar)
    if key == "last_push":
        return msg.last_push_message(webinar)
    raise KeyError(key)


async def run_broadcast(campaign_key: str, *, force: bool = False) -> tuple[int, int, int]:
    if bot is None:
        return 0, 0, 0
    if not force and await _campaign_already_sent(campaign_key):
        logger.info("Кампания %s уже отправлена — пропуск", campaign_key)
        return 0, 0, 0

    text = _campaign_text(campaign_key)
    chat_ids = await _list_active_chat_ids()
    ok = fail = 0

    async with AsyncSessionLocal() as db:
        # Резервируем ключ сразу, чтобы параллельный запуск не продублировал
        if not force:
            exists = await db.execute(
                select(WebinarBroadcastLog).where(
                    WebinarBroadcastLog.campaign_key == campaign_key
                )
            )
            if exists.scalar_one_or_none():
                return 0, 0, 0
        log = WebinarBroadcastLog(
            campaign_key=f"{campaign_key}_{int(datetime.now(timezone.utc).timestamp())}"
            if force
            else campaign_key,
            scheduled_at=datetime.now(MSK),
            recipients_total=len(chat_ids),
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        log_id = log.id

    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=False)
            ok += 1
        except Exception as e:
            fail += 1
            err = str(e).lower()
            logger.warning("Broadcast %s -> %s failed: %s", campaign_key, chat_id, e)
            if "blocked" in err or "deactivated" in err or "chat not found" in err:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(WebinarSubscriber).where(
                            WebinarSubscriber.telegram_id == chat_id
                        )
                    )
                    sub = result.scalar_one_or_none()
                    if sub:
                        sub.is_active = False
                        await db.commit()
        await asyncio.sleep(BROADCAST_PAUSE_SEC)

    async with AsyncSessionLocal() as db:
        log = await db.get(WebinarBroadcastLog, log_id)
        if log:
            log.recipients_ok = ok
            log.recipients_fail = fail
            log.finished_at = datetime.now(timezone.utc)
            await db.commit()

    logger.info(
        "Кампания %s завершена: total=%s ok=%s fail=%s",
        campaign_key,
        len(chat_ids),
        ok,
        fail,
    )
    return ok, fail, len(chat_ids)


CAMPAIGNS: dict[str, str] = {
    # key -> settings field name with schedule
    "announce": "WEBINAR_ANNOUNCE_AT",
    "remind": "WEBINAR_REMIND_AT",
    "last_push": "WEBINAR_LAST_PUSH_AT",
}


async def _wait_until(when: datetime) -> None:
    while True:
        now = datetime.now(MSK)
        delay = (when - now).total_seconds()
        if delay <= 0:
            return
        await asyncio.sleep(min(delay, 60))


async def _schedule_campaign(key: str, when: datetime) -> None:
    now = datetime.now(MSK)
    if when <= now:
        age = now - when
        if age <= timedelta(hours=CATCHUP_HOURS):
            logger.info("Catch-up рассылка %s (опоздание %s)", key, age)
            await run_broadcast(key)
        else:
            logger.info(
                "Пропуск %s: время %s уже прошло больше чем на %s ч",
                key,
                when.isoformat(),
                CATCHUP_HOURS,
            )
        return

    logger.info("Запланирована рассылка %s на %s (МСК)", key, when.isoformat())
    await _wait_until(when)
    await run_broadcast(key)


async def start_scheduler() -> None:
    tasks = []
    for key, attr in CAMPAIGNS.items():
        when = _parse_msk(getattr(settings, attr, "") or "")
        if when is None:
            logger.warning("Расписание для %s не задано (%s) — пропускаю", key, attr)
            continue
        tasks.append(asyncio.create_task(_schedule_campaign(key, when), name=f"wb_{key}"))
    if tasks:
        await asyncio.gather(*tasks)


async def main() -> None:
    if not settings.WEBINAR_BOT_ENABLED:
        logger.error("WEBINAR_BOT_ENABLED=false — выход")
        return
    if not settings.WEBINAR_BOT_TOKEN:
        logger.error("WEBINAR_BOT_TOKEN пустой — выход")
        return
    if bot is None:
        logger.error("Bot не инициализирован")
        return

    logger.info(
        "Webinar bot starting (proxy=%s)",
        "yes" if settings.TELEGRAM_API_BASE else "direct",
    )
    scheduler_task = asyncio.create_task(start_scheduler(), name="webinar_scheduler")
    try:
        await dp.start_polling(bot, polling_timeout=30, limit=5)
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
