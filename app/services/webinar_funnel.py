"""Персональная воронка webinar-бота: трекинг кликов по ролику и анонс."""
from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
from aiogram.enums import ParseMode
from sqlalchemy import select, update

from app.bot import webinar_messages as msg
from app.bot.httpx_session import HttpxSession
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.webinar import WebinarSubscriber

logger = logging.getLogger(__name__)

VIDEO_TARGETS = {
    "youtube": "https://clck.ru/3VdCaZ",
    "rutube": "https://clck.ru/3VdCdk",
}

ANNOUNCE_AFTER_LEAD_SEC = 60


def _sign(telegram_id: int, slug: str) -> str:
    payload = f"{telegram_id}:{slug}".encode()
    key = (settings.SECRET_KEY or "secret").encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:20]


def verify_track_sign(telegram_id: int, slug: str, signature: str) -> bool:
    expected = _sign(telegram_id, slug)
    return hmac.compare_digest(expected, (signature or "").strip())


def build_track_url(telegram_id: int, slug: str) -> str:
    base = (settings.SITE_URL or "").rstrip("/")
    qs = urlencode({"t": telegram_id, "s": _sign(telegram_id, slug)})
    return f"{base}/go/webinar/{slug}?{qs}"


def _build_bot() -> Bot | None:
    token = (settings.WEBINAR_BOT_TOKEN or "").strip()
    if not token:
        return None
    telegram_api = (
        TelegramAPIServer.from_base(settings.TELEGRAM_API_BASE.rstrip("/"))
        if settings.TELEGRAM_API_BASE
        else PRODUCTION
    )
    return Bot(
        token=token,
        session=HttpxSession(api=telegram_api),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def claim_funnel_announce(telegram_id: int) -> bool:
    """True — этот вызов должен отправить анонс (флаг выставлен атомарно)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(WebinarSubscriber)
            .where(
                WebinarSubscriber.telegram_id == telegram_id,
                WebinarSubscriber.funnel_announce_sent.is_(False),
            )
            .values(funnel_announce_sent=True)
            .returning(WebinarSubscriber.telegram_id)
        )
        claimed = result.scalar_one_or_none() is not None
        await db.commit()
        return claimed


async def send_funnel_announce_once(telegram_id: int) -> bool:
    """Отправить персональный announce один раз. False — уже отправляли / нет токена."""
    if not await claim_funnel_announce(telegram_id):
        return False
    bot = _build_bot()
    if bot is None:
        logger.error("WEBINAR_BOT_TOKEN пуст — funnel announce не отправлен")
        return False
    chat = (settings.WEBINAR_CHAT_INVITE_URL or "").strip()
    try:
        await bot.send_message(
            telegram_id,
            msg.announce_message(chat),
            disable_web_page_preview=False,
        )
        logger.info("Funnel announce sent to %s", telegram_id)
        return True
    except Exception:
        logger.exception("Funnel announce failed for %s — откатываю флаг", telegram_id)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(WebinarSubscriber)
                .where(WebinarSubscriber.telegram_id == telegram_id)
                .values(funnel_announce_sent=False)
            )
            await db.commit()
        return False
    finally:
        await bot.session.close()


async def mark_video_click(telegram_id: int, slug: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WebinarSubscriber).where(WebinarSubscriber.telegram_id == telegram_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            return
        sub.video_clicked = True
        sub.video_click_slug = slug[:32]
        await db.commit()
