"""Общий Jinja2 environment: глобальные переменные для всех HTML-шаблонов."""
from fastapi.templating import Jinja2Templates

from app.config import settings


def _telegram_username() -> str:
    return (settings.BOT_USERNAME or "").strip().lstrip("@")


_u = _telegram_username()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["telegram_bot_url"] = f"https://t.me/{_u}" if _u else "#"
templates.env.globals["telegram_bot_username"] = _u
templates.env.globals["instagram_url"] = (settings.INSTAGRAM_URL or "").strip()
templates.env.globals["tiktok_url"] = (settings.TIKTOK_URL or "").strip()
