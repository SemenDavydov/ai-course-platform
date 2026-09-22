"""Public waitlist (pre-enrollment) form."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.site import WaitlistApplication
from app.services.site_settings import (
    LANDING_MODE_ENROLLMENT,
    get_landing_mode,
)

router = APIRouter(tags=["waitlist"])


@router.post("/waitlist")
async def submit_waitlist(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    social: str = Form(""),
    accepted_privacy: Optional[str] = Form(None),
):
    mode = await get_landing_mode(db)
    if mode != LANDING_MODE_ENROLLMENT:
        return RedirectResponse(url="/#tariffs", status_code=303)

    name = (name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip().lower()
    social = (social or "").strip() or None
    privacy_ok = (accepted_privacy or "").lower() in ("true", "on", "1", "yes")

    if not name or not phone or not email or not privacy_ok:
        return RedirectResponse(url="/?waitlist=error#tariffs", status_code=303)
    if "@" not in email or "." not in email.split("@")[-1]:
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
