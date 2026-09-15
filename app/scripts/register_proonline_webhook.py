"""Однократно зарегистрировать webhook URL в ProOnline CRM.

Usage (на сервере, из корня проекта):
  cd /root/ai-course-platform
  source venv/bin/activate
  python -m app.scripts.register_proonline_webhook
"""
from __future__ import annotations

import asyncio
import sys

from app.config import settings
from app.services.proonline import ProOnlineClient, ProOnlineError


async def main() -> int:
    url = f"{settings.SITE_URL.rstrip('/')}/webhooks/proonline"
    secret = (settings.PROONLINE_WEBHOOK_SECRET or "").strip()
    if not secret:
        print("Задайте PROONLINE_WEBHOOK_SECRET в .env", file=sys.stderr)
        return 1
    client = ProOnlineClient()
    if not client.configured:
        print("Задайте PROONLINE_API_KEY в .env", file=sys.stderr)
        return 1
    try:
        result = await client.register_webhook(url=url, secret=secret)
    except ProOnlineError as exc:
        print(f"Ошибка: {exc} {exc.payload}", file=sys.stderr)
        return 1
    print("Webhook зарегистрирован:")
    print(f"  url = {url}")
    print(f"  response = {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
