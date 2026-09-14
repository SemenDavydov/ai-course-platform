"""Общий Jinja2 environment: глобальные переменные для всех HTML-шаблонов."""
from fastapi.templating import Jinja2Templates

from app.config import settings


def _telegram_username() -> str:
    return (settings.BOT_USERNAME or "").strip().lstrip("@")


def chat_invite_url_for(tariff_slug: str | None) -> str:
    """Invite-ссылка в закрытый TG-чат/канал по тарифу Pro/VIP."""
    slug = (tariff_slug or "").strip().lower()
    if slug == "vip":
        return (settings.VIP_CHAT_INVITE_URL or "").strip()
    if slug == "pro":
        return (settings.PRO_CHAT_INVITE_URL or "").strip()
    return ""


def chat_invite_label_for(tariff_slug: str | None) -> str:
    slug = (tariff_slug or "").strip().lower()
    if slug == "vip":
        return "Войти в VIP-канал"
    if slug == "pro":
        return "Войти в чат с куратором"
    return "Войти в Telegram"


_u = _telegram_username()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["telegram_bot_url"] = f"https://t.me/{_u}" if _u else "#"
templates.env.globals["telegram_bot_username"] = _u
templates.env.globals["instagram_url"] = (settings.INSTAGRAM_URL or "").strip()
templates.env.globals["tiktok_url"] = (settings.TIKTOK_URL or "").strip()
templates.env.globals["chat_invite_url_for"] = chat_invite_url_for
templates.env.globals["chat_invite_label_for"] = chat_invite_label_for
