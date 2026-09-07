"""Редиректы для трекинга кликов по роликам webinar-бота."""
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.services.webinar_funnel import (
    VIDEO_TARGETS,
    mark_video_click,
    send_funnel_announce_once,
    verify_track_sign,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webinar-funnel"])


@router.get("/go/webinar/{slug}")
async def webinar_video_redirect(
    slug: str,
    t: int = Query(..., description="telegram_id"),
    s: str = Query(..., description="signature"),
):
    if slug not in VIDEO_TARGETS:
        raise HTTPException(status_code=404, detail="Unknown video")
    if not verify_track_sign(t, slug, s):
        logger.warning("Invalid webinar track signature t=%s slug=%s", t, slug)
        raise HTTPException(status_code=403, detail="Invalid signature")

    logger.info("Webinar video click t=%s slug=%s", t, slug)
    await mark_video_click(t, slug)
    sent = await send_funnel_announce_once(t)
    logger.info("Webinar funnel announce after click t=%s sent=%s", t, sent)
    return RedirectResponse(VIDEO_TARGETS[slug], status_code=302)
