"""Helpers for landing mode and community invite URL."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.site import SiteSetting

LANDING_MODE_KEY = "landing_mode"
LANDING_MODE_ENROLLMENT = "enrollment"
LANDING_MODE_SALES = "sales"
DEFAULT_LANDING_MODE = LANDING_MODE_ENROLLMENT
VALID_LANDING_MODES = frozenset({LANDING_MODE_ENROLLMENT, LANDING_MODE_SALES})


async def get_landing_mode(db: AsyncSession) -> str:
    row = await db.get(SiteSetting, LANDING_MODE_KEY)
    if row and row.value in VALID_LANDING_MODES:
        return row.value
    return DEFAULT_LANDING_MODE


async def set_landing_mode(db: AsyncSession, mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in VALID_LANDING_MODES:
        raise ValueError(f"Invalid landing mode: {mode}")
    row = await db.get(SiteSetting, LANDING_MODE_KEY)
    if row is None:
        row = SiteSetting(key=LANDING_MODE_KEY, value=mode)
        db.add(row)
    else:
        row.value = mode
    await db.commit()
    return mode


def community_invite_url() -> str:
    url = (settings.WEBINAR_CHAT_INVITE_URL or "").strip()
    return url or "https://t.me/ai_story_news"
