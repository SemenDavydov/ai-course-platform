"""Разовая рассылка webinar-бота: открытие набора.

Запуск на сервере:
  cd /root/ai-course-platform
  venv/bin/python -m scripts.send_webinar_open_set
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# корень проекта в PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.enums import ParseMode
from sqlalchemy import select

from app.bot.httpx_session import HttpxSession
from app.bot.webinar_messages import open_set_message
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.webinar import WebinarBroadcastLog, WebinarSubscriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("send_open_set")

PAUSE_SEC = 0.05
CAMPAIGN_KEY = "open_set"


async def main() -> None:
    if not settings.WEBINAR_BOT_TOKEN:
        raise SystemExit("WEBINAR_BOT_TOKEN пустой")

    text = open_set_message()
    telegram_api = (
        TelegramAPIServer.from_base(settings.TELEGRAM_API_BASE.rstrip("/"))
        if settings.TELEGRAM_API_BASE
        else PRODUCTION
    )
    bot = Bot(
        token=settings.WEBINAR_BOT_TOKEN,
        session=HttpxSession(api=telegram_api),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    async with AsyncSessionLocal() as db:
        chat_ids = list(
            (
                await db.execute(
                    select(WebinarSubscriber.telegram_id).where(
                        WebinarSubscriber.is_active == True  # noqa: E712
                    )
                )
            ).scalars().all()
        )
        log = WebinarBroadcastLog(
            campaign_key=f"{CAMPAIGN_KEY}_{int(datetime.now(timezone.utc).timestamp())}",
            scheduled_at=datetime.now(timezone.utc),
            recipients_total=len(chat_ids),
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        log_id = log.id

    logger.info("Подписчиков: %s. Начинаю рассылку…", len(chat_ids))
    ok = fail = 0
    try:
        for i, chat_id in enumerate(chat_ids, start=1):
            try:
                await bot.send_message(chat_id, text, disable_web_page_preview=False)
                ok += 1
            except Exception as e:
                fail += 1
                err = str(e).lower()
                logger.warning("fail %s: %s", chat_id, e)
                if "blocked" in err or "deactivated" in err or "chat not found" in err:
                    async with AsyncSessionLocal() as db:
                        sub = (
                            await db.execute(
                                select(WebinarSubscriber).where(
                                    WebinarSubscriber.telegram_id == chat_id
                                )
                            )
                        ).scalar_one_or_none()
                        if sub:
                            sub.is_active = False
                            await db.commit()
            if i % 50 == 0:
                logger.info("Прогресс %s/%s (ok=%s fail=%s)", i, len(chat_ids), ok, fail)
            await asyncio.sleep(PAUSE_SEC)
    finally:
        await bot.session.close()

    async with AsyncSessionLocal() as db:
        log = await db.get(WebinarBroadcastLog, log_id)
        if log:
            log.recipients_ok = ok
            log.recipients_fail = fail
            log.finished_at = datetime.now(timezone.utc)
            await db.commit()

    logger.info("Готово: total=%s ok=%s fail=%s", len(chat_ids), ok, fail)


if __name__ == "__main__":
    asyncio.run(main())
