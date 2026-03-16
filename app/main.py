from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi import Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse

from app.api import webhooks
from app.api import admin
from app.api.v1 import materials
from app.api.v1 import bot_api
from app.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.APP_NAME)

templates = Jinja2Templates(directory="app/templates")

# Добавь middleware для сессий (ВАЖНО: добавить до роутеров)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="admin_session",
    max_age=86400,
    same_site="lax",
    https_only=False  # False для localhost
)

# Подключаем роутеры
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(bot_api.router)
app.include_router(materials.router)

@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "status": "running",
        "debug": settings.DEBUG
    }

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