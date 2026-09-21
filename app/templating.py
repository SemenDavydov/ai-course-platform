"""Общий Jinja2 environment: глобальные переменные для всех HTML-шаблонов."""
from __future__ import annotations

from typing import Any

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

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


class CompatibleJinja2Templates(Jinja2Templates):
    """Поддержка и старого, и нового API TemplateResponse.

    Starlette <1: TemplateResponse(name, {"request": request, ...})
    Starlette 1+: TemplateResponse(request, name, context)

    Без этого на Starlette 1.x главная падает с:
    TypeError: unhashable type: 'dict'
    """

    def TemplateResponse(self, *args: Any, **kwargs: Any):
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", {})
            if not isinstance(context, dict):
                raise TypeError("TemplateResponse context must be a dict")
            request = context.get("request")
            if not isinstance(request, Request):
                raise ValueError('context must include a Starlette "request" key')
            status_code = args[2] if len(args) > 2 else kwargs.pop("status_code", 200)
            headers = args[3] if len(args) > 3 else kwargs.pop("headers", None)
            media_type = args[4] if len(args) > 4 else kwargs.pop("media_type", None)
            background = args[5] if len(args) > 5 else kwargs.pop("background", None)
            # Новый порядок аргументов (Starlette 1.x / актуальный FastAPI).
            try:
                return super().TemplateResponse(
                    request,
                    name,
                    context,
                    status_code=status_code,
                    headers=headers,
                    media_type=media_type,
                    background=background,
                    **kwargs,
                )
            except TypeError:
                # Fallback на старый порядок, если установлен Starlette 0.52.x.
                return super().TemplateResponse(
                    name,
                    context,
                    status_code=status_code,
                    headers=headers,
                    media_type=media_type,
                    background=background,
                    **kwargs,
                )
        return super().TemplateResponse(*args, **kwargs)


_u = _telegram_username()
templates = CompatibleJinja2Templates(directory="app/templates")
templates.env.globals["telegram_bot_url"] = f"https://t.me/{_u}" if _u else "#"
templates.env.globals["telegram_bot_username"] = _u
templates.env.globals["instagram_url"] = (settings.INSTAGRAM_URL or "").strip()
templates.env.globals["tiktok_url"] = (settings.TIKTOK_URL or "").strip()
templates.env.globals["chat_invite_url_for"] = chat_invite_url_for
templates.env.globals["chat_invite_label_for"] = chat_invite_label_for
