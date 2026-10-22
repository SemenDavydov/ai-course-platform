"""Helpers for landing mode, sales schedule and community invite URL."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.site import SiteSetting

# Москва — UTC+3 круглый год.
MSK = timezone(timedelta(hours=3))

SALES_ENABLED_KEY = "sales_schedule_enabled"
SALES_START_KEY = "sales_start"
SALES_END_KEY = "sales_end"

PHASE_BEFORE = "before"   # до старта: предзапись + отсчёт «до старта продаж»
PHASE_OPEN = "open"       # продажи идут: тарифы + отсчёт «до конца продаж»
PHASE_CLOSED = "closed"   # продажи завершены: предзапись

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


@dataclass
class SalesSchedule:
    enabled: bool = False
    start: datetime | None = None  # UTC
    end: datetime | None = None    # UTC

    @property
    def active(self) -> bool:
        """Расписание включено и полностью заполнено — оно управляет продажами."""
        return bool(self.enabled and self.start and self.end and self.end > self.start)

    def phase(self, now: datetime | None = None) -> str | None:
        if not self.active:
            return None
        now = now or datetime.now(timezone.utc)
        if now < self.start:
            return PHASE_BEFORE
        if now < self.end:
            return PHASE_OPEN
        return PHASE_CLOSED

    def countdown_target(self, now: datetime | None = None) -> datetime | None:
        """К какому моменту идёт отсчёт: старт (до начала) или конец (во время продаж)."""
        phase = self.phase(now)
        if phase == PHASE_BEFORE:
            return self.start
        if phase == PHASE_OPEN:
            return self.end
        return None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _get_value(db: AsyncSession, key: str) -> str | None:
    row = await db.get(SiteSetting, key)
    return row.value if row else None


async def _set_value(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(SiteSetting, key)
    if row is None:
        db.add(SiteSetting(key=key, value=value))
    else:
        row.value = value


async def get_sales_schedule(db: AsyncSession) -> SalesSchedule:
    return SalesSchedule(
        enabled=(await _get_value(db, SALES_ENABLED_KEY)) == "1",
        start=_parse_utc(await _get_value(db, SALES_START_KEY)),
        end=_parse_utc(await _get_value(db, SALES_END_KEY)),
    )


async def set_sales_schedule(
    db: AsyncSession, *, enabled: bool, start: datetime | None, end: datetime | None
) -> SalesSchedule:
    """Сохраняет расписание. Даты — aware datetime (любая зона), храним в UTC."""
    if enabled and (start is None or end is None):
        raise ValueError("Укажите дату начала и дату окончания продаж")
    if start and end and end <= start:
        raise ValueError("Дата окончания должна быть позже даты начала")
    await _set_value(db, SALES_ENABLED_KEY, "1" if enabled else "0")
    await _set_value(db, SALES_START_KEY, start.astimezone(timezone.utc).isoformat() if start else "")
    await _set_value(db, SALES_END_KEY, end.astimezone(timezone.utc).isoformat() if end else "")
    await db.commit()
    return await get_sales_schedule(db)


async def effective_landing_mode(db: AsyncSession, now: datetime | None = None) -> str:
    """Режим лендинга с учётом расписания: оно важнее ручного переключателя."""
    phase = (await get_sales_schedule(db)).phase(now)
    if phase == PHASE_OPEN:
        return LANDING_MODE_SALES
    if phase in (PHASE_BEFORE, PHASE_CLOSED):
        return LANDING_MODE_ENROLLMENT
    return await get_landing_mode(db)


async def new_sales_closed_reason(db: AsyncSession, now: datetime | None = None) -> str | None:
    """Почему сейчас нельзя оформить НОВУЮ покупку (None — можно).
    Без расписания ограничений нет (как раньше); апгрейды сюда не относятся."""
    schedule = await get_sales_schedule(db)
    phase = schedule.phase(now)
    if phase == PHASE_BEFORE:
        return (
            "Продажи сейчас закрыты. Старт продаж — "
            f"{format_msk(schedule.start)} МСК."
        )
    if phase == PHASE_CLOSED:
        return "Продажи сейчас закрыты. Оставьте заявку на сайте — мы сообщим о следующем потоке."
    return None


_MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def format_msk(dt: datetime | None, *, long: bool = False) -> str:
    """01.11.2026 12:00 или «1 ноября 2026, 12:00» (long) — по Москве."""
    if dt is None:
        return ""
    m = dt.astimezone(MSK)
    if long:
        return f"{m.day} {_MONTHS_GEN[m.month - 1]} {m.year}, {m:%H:%M}"
    return m.strftime("%d.%m.%Y %H:%M")


def community_invite_url() -> str:
    url = (settings.WEBINAR_CHAT_INVITE_URL or "").strip()
    return url or "https://t.me/ai_story_news"
