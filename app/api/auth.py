"""
Auth router: регистрация, вход, выход, верификация email, сброс/установка пароля.
Prefix: /auth
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.config import settings
from app.models.admin_session import AdminSession
from app.models.lesson_progress import LessonProgress
from app.models.payment import Payment
from app.models.user_session import UserSession
from app.services import auth as auth_service
from app.tasks import enqueue_email

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 дней


# ---------------------------------------------------------------------------
# Схемы запросов
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    accepted_offer: bool = False

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Пароль должен быть не короче 8 символов")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EmailRequest(BaseModel):
    email: EmailStr


class TokenPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Пароль должен быть не короче 8 символов")
        return v


# ---------------------------------------------------------------------------
# Хелпер: установить cookie сессии
# ---------------------------------------------------------------------------

def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="user_session",
        value=token,
        max_age=SESSION_COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------

@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    if not body.accepted_offer:
        raise HTTPException(status_code=422, detail="Необходимо принять оферту")

    try:
        user = await auth_service.register_user(
            db, body.email, body.password, body.accepted_offer
        )
    except ValueError as e:
        if str(e) == "email_taken":
            raise HTTPException(status_code=409, detail="Email уже зарегистрирован")
        raise

    session_token = await auth_service.create_session(db, user)
    _set_session_cookie(response, session_token)

    # Email-верификация не нужна для доступа к курсу, но нужна для чувствительных действий.
    payload = await auth_service.prepare_email_verification(db, user)
    email_sent = False
    if payload:
        to, subject, html = payload
        email_sent = enqueue_email(to, subject, html)

    detail = (
        "Аккаунт создан. Письмо для подтверждения email отправлено."
        if email_sent
        else "Аккаунт создан. Письмо подтверждения пока не отправлено, попробуйте позже из профиля."
    )
    return {"detail": detail, "user_id": user.id, "email_sent": email_sent}


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    try:
        user = await auth_service.login(db, body.email, body.password)
    except ValueError as e:
        error = str(e)
        if error == "not_found":
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        if error == "password_not_set":
            raise HTTPException(
                status_code=409,
                detail="Пароль не установлен. Запросите письмо для установки пароля.",
                headers={"X-Auth-Action": "setup_password"},
            )
        raise

    if user is None:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    session_token = await auth_service.create_session(db, user)
    _set_session_cookie(response, session_token)
    return {"detail": "OK", "user_id": user.id}


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session_token = request.cookies.get("user_session")
    if session_token:
        await db.execute(
            delete(UserSession).where(UserSession.session_token == session_token)
        )
        await db.commit()
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(key="user_session", path="/")
    return resp


# ---------------------------------------------------------------------------
# POST /auth/delete-account
# ---------------------------------------------------------------------------

@router.post("/delete-account")
async def delete_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        user = await auth_service.get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=303)

    # Чувствительное действие: требуем подтверждённый email
    if not user.email_verified:
        return RedirectResponse("/cabinet/profile?error=email_not_verified", status_code=303)

    uid = user.id
    await db.execute(delete(Payment).where(Payment.user_id == uid))
    await db.execute(delete(LessonProgress).where(LessonProgress.user_id == uid))
    await db.execute(delete(UserSession).where(UserSession.user_id == uid))
    await db.execute(delete(AdminSession).where(AdminSession.user_id == uid))
    await db.delete(user)
    await db.commit()

    resp = RedirectResponse("/?account_deleted=1", status_code=303)
    resp.delete_cookie(key="user_session", path="/")
    return resp


# ---------------------------------------------------------------------------
# GET /auth/verify-email?token=...
# ---------------------------------------------------------------------------

@router.get("/verify-email")
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.verify_email(db, token)
    if not user:
        return RedirectResponse(url="/?auth_error=invalid_token", status_code=302)
    return RedirectResponse(url="/?auth_message=email_verified", status_code=302)


# ---------------------------------------------------------------------------
# GET /auth/telegram-login?token=...
# ---------------------------------------------------------------------------

@router.get("/telegram-login")
async def telegram_login(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Вход по одноразовой ссылке из бота — для покупателей, у которых нет email.
    Токен сгорает при первом переходе, дальше работает обычная cookie-сессия.
    """
    user = await auth_service.consume_login_token(db, token)
    if not user:
        return RedirectResponse("/?auth_error=login_link_expired", status_code=303)

    session_token = await auth_service.create_session(db, user)

    target = "/cabinet/lessons" if user.has_access else "/"
    resp = RedirectResponse(target, status_code=303)
    _set_session_cookie(resp, session_token)
    return resp


# ---------------------------------------------------------------------------
# POST /auth/resend-verification
# ---------------------------------------------------------------------------

@router.post("/resend-verification")
async def resend_verification(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.get_current_user(request, db)
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Email уже подтверждён")
    payload = await auth_service.resend_verification(db, user)
    email_sent = False
    if payload:
        to, subject, html = payload
        email_sent = enqueue_email(to, subject, html)
    if email_sent:
        return {"detail": "Письмо отправлено", "email_sent": True}
    return {"detail": "Не удалось отправить письмо. Проверьте SMTP/соединение и попробуйте снова.", "email_sent": False}


# ---------------------------------------------------------------------------
# POST /auth/password-reset/request
# ---------------------------------------------------------------------------

@router.post("/password-reset/request")
async def password_reset_request(
    body: EmailRequest,
    db: AsyncSession = Depends(get_db),
):
    payload = await auth_service.prepare_password_reset_email(db, body.email)
    if payload:
        to, subject, html = payload
        enqueue_email(to, subject, html)
    # Всегда возвращаем 200 — не раскрываем наличие аккаунта
    return {"detail": "Если email зарегистрирован, письмо отправлено"}


# ---------------------------------------------------------------------------
# POST /auth/password-reset/confirm
# ---------------------------------------------------------------------------

@router.post("/password-reset/confirm")
async def password_reset_confirm(
    body: TokenPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    ok = await auth_service.reset_password(db, body.token, body.password)
    if not ok:
        raise HTTPException(status_code=400, detail="Ссылка недействительна или истекла")
    return {"detail": "Пароль изменён"}


# ---------------------------------------------------------------------------
# POST /auth/password-setup/request  (для мигрированных пользователей)
# ---------------------------------------------------------------------------

@router.post("/password-setup/request")
async def password_setup_request(
    body: EmailRequest,
    db: AsyncSession = Depends(get_db),
):
    payload = await auth_service.prepare_password_setup_email(db, body.email)
    if payload:
        to, subject, html = payload
        enqueue_email(to, subject, html)
    return {"detail": "Если аккаунт найден, письмо отправлено"}


# ---------------------------------------------------------------------------
# POST /auth/password-setup/confirm
# ---------------------------------------------------------------------------

@router.post("/password-setup/confirm")
async def password_setup_confirm(
    body: TokenPasswordRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ok = await auth_service.confirm_password_setup(db, body.token, body.password)
    if not ok:
        raise HTTPException(status_code=400, detail="Ссылка недействительна или истекла")
    return {"detail": "Пароль установлен. Теперь вы можете войти."}
