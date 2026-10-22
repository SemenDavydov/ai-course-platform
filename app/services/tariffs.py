"""Тарифы: slug'и, ранги, сроки доступа, названия — единый источник правды."""
from __future__ import annotations

from datetime import datetime, timedelta

LEGACY = "legacy"
SELF = "self"
PRO = "pro"
VIP = "vip"

# Что можно купить через сайт/бота.
PURCHASABLE_SLUGS = (SELF, PRO, VIP)
# Рассрочка ProOnline — только Pro/VIP; «Самостоятельный» — только оплата сразу.
INSTALLMENT_SLUGS = (PRO, VIP)

# «Самостоятельный»: доступ ко всем материалам на 2 месяца с момента оплаты.
SELF_ACCESS_DAYS = 60
# Письмо «доступ скоро закончится» — за столько дней до конца.
EXPIRY_REMINDER_DAYS = 7

RANK = {LEGACY: 0, SELF: 1, PRO: 2, VIP: 3}

LABELS = {
    SELF: "Самостоятельный",
    PRO: "Pro",
    VIP: "VIP",
    LEGACY: "Классический",
}


def tariff_rank(slug: str | None) -> int:
    return RANK.get((slug or "").strip().lower(), 0)


def tariff_label(slug: str | None) -> str:
    s = (slug or "").strip().lower()
    return LABELS.get(s, s.upper() if s else "")


def access_expiry_for(slug: str, now: datetime) -> datetime | None:
    """Срок доступа для нового гранта: None = бессрочно."""
    if (slug or "").strip().lower() == SELF:
        return now + timedelta(days=SELF_ACCESS_DAYS)
    return None
