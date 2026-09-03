import json
from fastapi import FastAPI, Depends
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, FileResponse
from sqlalchemy import select

from app.api import webhooks, admin, auth, cabinet
from app.api.v1 import bot_api, materials, payments, progress
from app.config import settings
from app.database import get_db
from app.models.payment import Payment
from app.models.course import Course, Module, Tariff
from app.services.auth import get_optional_user
from app.services.access import get_primary_course, list_user_accesses
from app.templating import templates
from sqlalchemy.orm import selectinload
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.APP_NAME)

# Добавь middleware для сессий (ВАЖНО: добавить до роутеров)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="admin_session",
    max_age=86400,
    same_site="lax",
    https_only=settings.cookie_secure,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Подключаем роутеры
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(cabinet.router)
app.include_router(bot_api.router)
app.include_router(materials.router)
app.include_router(payments.router)
app.include_router(progress.router)

@app.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    current_user=Depends(get_optional_user),
    db=Depends(get_db),
):
    user_json = "null"
    has_story_access = False
    if current_user:
        accesses = await list_user_accesses(db, current_user.id)
        has_story_access = any(
            a.course and a.course.slug == "ai-story" for a in accesses
        )
        user_json = json.dumps({
            "id": current_user.id,
            "email": current_user.email,
            "email_verified": current_user.email_verified,
            "has_access": current_user.has_access,
            "has_story_access": has_story_access,
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

    return templates.TemplateResponse("landing.html", {
        "request": request,
        "app_name": settings.APP_NAME,
        "current_user": current_user,
        "current_user_json": user_json,
        "course": course,
        "modules": modules,
        "tariffs": tariffs,
        "has_story_access": has_story_access,
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