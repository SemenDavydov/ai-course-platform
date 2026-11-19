"""Public waitlist (pre-enrollment) form."""
from __future__ import annotations

import re
from typing import Optional

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.site import WaitlistApplication
from app.services import ratelimit
from app.services.site_settings import (
    LANDING_MODE_ENROLLMENT,
    effective_landing_mode,
)

router = APIRouter(tags=["waitlist"])

PHONE_RE = re.compile(r"\+?[\d\s()\-]{6,20}")
SPAM_RE = re.compile(r"https?://|www\.|<|>|\[url", re.IGNORECASE)


@router.post("/waitlist")
async def submit_waitlist(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    social: str = Form(""),
    accepted_privacy: Optional[str] = Form(None),
    website: str = Form(""),  # ловушка: скрыто от людей, боты заполняют
):
    mode = await effective_landing_mode(db)
    if mode != LANDING_MODE_ENROLLMENT:
        return RedirectResponse(url="/#tariffs", status_code=303)

    try:
        await ratelimit.check(ratelimit.WAITLIST_IP, ratelimit.client_ip(request))
    except ratelimit.RateLimitExceeded:
        return RedirectResponse(url="/?waitlist=error#tariffs", status_code=303)

    name = (name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip().lower()
    social = (social or "").strip() or None
    privacy_ok = (accepted_privacy or "").lower() in ("true", "on", "1", "yes")

    if website:
        # Бот заполнил скрытое поле: делаем вид, что всё ок, и ничего не сохраняем.
        return RedirectResponse(url="/?waitlist=ok#tariffs", status_code=303)
    if not name or not phone or not email or not privacy_ok:
        return RedirectResponse(url="/?waitlist=error#tariffs", status_code=303)
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return RedirectResponse(url="/?waitlist=error#tariffs", status_code=303)
    # Спам-боты шлют ссылки и HTML (проверяют, где их отрисуют кликабельными).
    if not PHONE_RE.fullmatch(phone) or any(SPAM_RE.search(v) for v in (name, social or "")):
        return RedirectResponse(url="/?waitlist=error#tariffs", status_code=303)

    db.add(
        WaitlistApplication(
            name=name[:255],
            phone=phone[:64],
            email=email[:255],
            social=(social[:255] if social else None),
            accepted_privacy=True,
        )
    )
    await db.commit()
    return RedirectResponse(url="/?waitlist=ok#tariffs", status_code=303)
