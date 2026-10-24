import json
from datetime import datetime, timezone

from fastapi import FastAPI, Depends
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, FileResponse, Response
from sqlalchemy import select

from app.api import webhooks, admin, auth, cabinet, webinar_track, waitlist
from app.api.v1 import bot_api, materials, payments, progress
from app.config import settings
from app.database import get_db
from app.models.payment import Payment
from app.models.course import Course, Module, Tariff
from app.services.auth import get_optional_user
from app.services.access import (
    access_is_active,
    format_opening,
    lessons_opening_at,
    get_primary_course,
    list_user_accesses,
    paid_credit,
    purchase_block_reason,
    upgrade_price,
)
from app.services import tariffs as tariffs_mod
from app.services.site_settings import (
    LANDING_MODE_SALES,
    PHASE_OPEN,
    SalesSchedule,
    community_invite_url,
    effective_landing_mode,
    format_msk,
    get_sales_schedule,
)
from app.templating import templates
from sqlalchemy.orm import selectinload
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Swagger/OpenAPI только в DEBUG: на проде /docs раскрывал всем карту API.
app = FastAPI(
    title=settings.APP_NAME,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# Добавь middleware для сессий (ВАЖНО: добавить до роутеров)
# Имя cookie НЕ должно совпадать с admin_session — это сессия админки из БД.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="app_session",
    max_age=86400,
    same_site="lax",
    https_only=settings.cookie_secure,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def block_legacy_course_videos(request: Request, call_next):
    # Старые MP4 лендинга (~30–40 МБ каждый) с телефона забивают канал VPS.
    if request.url.path.startswith("/static/videos/"):
        return Response(status_code=404)
    return await call_next(request)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response

# Подключаем роутеры
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(cabinet.router)
app.include_router(bot_api.router)
app.include_router(materials.router)
app.include_router(payments.router)
app.include_router(progress.router)
app.include_router(webinar_track.router)
app.include_router(waitlist.router)

def _countdown_context(schedule: SalesSchedule, now: datetime) -> dict | None:
    """Данные блока отсчёта: до старта продаж или до их конца (None — не показываем)."""
    phase = schedule.phase(now)
    target = schedule.countdown_target(now)
    if not target:
        return None
    total = (schedule.end - schedule.start).total_seconds()
    progress = 0.0
    if phase == PHASE_OPEN and total > 0:
        progress = min(100.0, max(0.0, (now - schedule.start).total_seconds() / total * 100))
    if phase == PHASE_OPEN:
        title = "До конца продаж осталось"
        when_text = f"Продажи до {format_msk(schedule.end, long=True)} МСК"
        strip_label, strip_cta, strip_href = "До конца продаж", "Выбрать тариф", "#tariffs"
    else:
        title = "До старта продаж осталось"
        when_text = f"Старт продаж {format_msk(schedule.start, long=True)} МСК"
        strip_label, strip_cta, strip_href = "До старта продаж", "Записаться", "#tariffs"
    return {
        "phase": phase,
        "target_iso": target.isoformat(),
        "server_now_iso": now.isoformat(),
        "start_iso": schedule.start.isoformat(),
        "end_iso": schedule.end.isoformat(),
        "start_short": format_msk(schedule.start)[:5],
        "end_short": format_msk(schedule.end)[:5],
        "progress": round(progress, 2),
        "title": title,
        "when_text": when_text,
        "strip_label": strip_label,
        "strip_cta": strip_cta,
        "strip_href": strip_href,
    }


@app.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    current_user=Depends(get_optional_user),
    db=Depends(get_db),
):
    user_json = "null"
    has_story_access = False
    story_tariff_slug = ""
    story_access = None
    story_access_until = ""
    story_lessons_open_label = ""
    self_expired = False
    if current_user:
        accesses = await list_user_accesses(db, current_user.id, include_expired=True)
        story_access = next(
            (a for a in accesses if a.course and a.course.slug == "ai-story"),
            None,
        )
        has_story_access = access_is_active(story_access)
        # Срок «Самостоятельного» вышел: повторно его не продаём, только Pro/VIP.
        self_expired = (
            story_access is not None
            and not has_story_access
            and story_access.tariff_slug == tariffs_mod.SELF
        )
        story_tariff_slug = (story_access.tariff_slug if has_story_access else "") or ""
        if has_story_access and story_access.expires_at:
            story_access_until = story_access.expires_at.strftime("%d.%m.%Y")
        opening = lessons_opening_at(story_access) if has_story_access else None
        if opening:
            story_lessons_open_label = format_opening(opening)
        user_json = json.dumps({
            "id": current_user.id,
            "email": current_user.email,
            "email_verified": current_user.email_verified,
            "has_access": current_user.has_access,
            "has_story_access": has_story_access,
            "story_tariff_slug": story_tariff_slug,
            "accepted_offer": current_user.accepted_offer,
        })

    course = await get_primary_course(db)
    modules = []
    tariffs = []
    if course:
        m_result = await db.execute(
            select(Module)
            .where(Module.course_id == course.id)
            .options(selectinload(Module.lessons))
            .order_by(Module.order)
        )
        modules = list(m_result.scalars().unique().all())
        t_result = await db.execute(
            select(Tariff)
            .where(Tariff.course_id == course.id, Tariff.is_active == True)
            .order_by(Tariff.sort_order, Tariff.price)
        )
        tariffs = list(t_result.scalars().all())
        if story_access is not None:
            # Истёкший self → только Pro/VIP; действующий self → апгрейд до Pro/VIP.
            tariffs = [
                t for t in tariffs if purchase_block_reason(story_access, t.slug) is None
            ]

    # Расписание продаж (если включено) важнее ручного переключателя режима.
    landing_mode = await effective_landing_mode(db)
    waitlist_status = request.query_params.get("waitlist")
    # Ученик на «Самостоятельном» видит и «Доступ открыт», и тарифы для апгрейда.
    can_upgrade = (
        has_story_access
        and story_tariff_slug == tariffs_mod.SELF
        and bool(tariffs)
        and landing_mode == LANDING_MODE_SALES
    )
    # Отсчёт видят те, кому есть что купить: без доступа или с апгрейдом.
    countdown = None
    if not has_story_access or can_upgrade:
        countdown = _countdown_context(await get_sales_schedule(db), datetime.now(timezone.utc))

    # Цены апгрейда с зачётом оплаченного: {slug: сумма к оплате}.
    upgrade_credit = 0.0
    upgrade_prices: dict[str, float] = {}
    if can_upgrade and course:
        upgrade_credit = await paid_credit(db, current_user.id, course.id, story_access)
        if upgrade_credit > 0:
            upgrade_prices = {
                t.slug: upgrade_price(t.price, upgrade_credit) for t in tariffs
            }

    return templates.TemplateResponse("landing.html", {
        "request": request,
        "app_name": settings.APP_NAME,
        "current_user": current_user,
        "current_user_json": user_json,
        "course": course,
        "modules": modules,
        "tariffs": tariffs,
        "has_story_access": has_story_access,
        "story_tariff_slug": story_tariff_slug,
        "self_expired": self_expired,
        "can_upgrade": can_upgrade,
        "countdown": countdown,
        "upgrade_credit": upgrade_credit,
        "upgrade_prices": upgrade_prices,
        "story_access_until": story_access_until,
        "story_lessons_open_label": story_lessons_open_label,
        "installment_slugs": tariffs_mod.INSTALLMENT_SLUGS,
        "landing_mode": landing_mode,
        "community_invite_url": community_invite_url(),
        "waitlist_status": waitlist_status,
    })

@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse("app/static/robots.txt", media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    return FileResponse("app/static/sitemap.xml", media_type="application/xml")


@app.get("/payment/success", response_class=HTMLResponse)
async def payment_success(
    request: Request,
    current_user=Depends(get_optional_user),
    db=Depends(get_db),
):
    status = "pending"
    if current_user:
        result = await db.execute(
            select(Payment)
            .where(Payment.user_id == current_user.id)
            .order_by(Payment.created_at.desc())
        )
        last_payment = result.scalars().first()
        if last_payment and last_payment.status == "succeeded":
            status = "succeeded"

    return templates.TemplateResponse("payment_success.html", {
        "request": request,
        "status": status,
    })


@app.get("/payment/failure", response_class=HTMLResponse)
async def payment_failure(request: Request):
    return templates.TemplateResponse("payment_failure.html", {"request": request})


@app.get("/offer", response_class=HTMLResponse)
async def show_offer(request: Request):
    return templates.TemplateResponse("legal/offer.html", {"request": request})

@app.get("/privacy", response_class=HTMLResponse)
async def show_privacy(request: Request):
    return templates.TemplateResponse("legal/privacy.html", {"request": request})

@app.on_event("startup")
async def startup_event():
    logger.info("Starting up...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down...")