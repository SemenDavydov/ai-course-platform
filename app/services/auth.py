"""
Auth service: регистрация, вход, сессии, верификация email, сброс/установка пароля.
"""
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import Cookie, Depends, HTTPException, Request

from app.database import get_db
from app.models.user import User
from app.models.user_session import UserSession
from app.config import settings

logger = logging.getLogger(__name__)

SESSION_LIFETIME_DAYS = 30
EMAIL_VERIFY_EXPIRY_HOURS = 24
PASSWORD_RESET_EXPIRY_HOURS = 1
PASSWORD_SETUP_EXPIRY_DAYS = 7
LOGIN_TOKEN_EXPIRY_MINUTES = 15

# Jinja2 env для email-шаблонов (синхронный, для Celery-задач)
_jinja_env = Environment(
    loader=FileSystemLoader("app/templates/emails"),
    autoescape=select_autoescape(["html"]),
)


def _render(template_name: str, **kwargs) -> str:
    return _jinja_env.get_template(template_name).render(**kwargs)


# ---------------------------------------------------------------------------
# Регистрация
# ---------------------------------------------------------------------------

async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    accepted_offer: bool,
) -> User:
    """
    Создаёт нового веб-пользователя.
    Email НЕ считается подтверждённым автоматически: подтверждение нужно только
    для чувствительных действий (смена email/удаление аккаунта), но не для доступа к курсу.
    Raises ValueError("email_taken") если email уже занят.
    """
    result = await db.execute(
        select(User)
        .where(User.email == email)
        .order_by(User.id.desc())
        .limit(1)
    )
    if result.scalars().first():
        raise ValueError("email_taken")

    user = User(
        email=email,
        email_verified=False,
        registration_source="web",
        accepted_offer=accepted_offer,
    )
    user.set_password(password)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return user


# ---------------------------------------------------------------------------
# Верификация email: подготовка письма
# ---------------------------------------------------------------------------

async def prepare_email_verification(db: AsyncSession, user: User) -> Optional[tuple[str, str, str]]:
    """
    Генерирует токен подтверждения email и возвращает данные письма:
    (to, subject, html_body). Возвращает None если отправлять нечего.
    """
    if not user.email:
        return None
    if user.email_verified:
        return None

    token = secrets.token_urlsafe(32)
    user.email_verification_token = token
    user.email_verification_sent_at = datetime.now(timezone.utc)
    await db.commit()

    verification_url = f"{settings.SITE_URL}/auth/verify-email?token={token}"
    html = _render("verify_email.html", verification_url=verification_url)
    return (user.email, "Подтвердите ваш email", html)


# ---------------------------------------------------------------------------
# Подтверждение email
# ---------------------------------------------------------------------------

async def verify_email(db: AsyncSession, token: str) -> Optional[User]:
    """
    Помечает email как подтверждённый. Возвращает User или None если токен не найден/истёк.
    """
    result = await db.execute(
        select(User).where(User.email_verification_token == token)
    )
    user = result.scalar_one_or_none()
    if not user:
        return None

    # Проверяем срок годности токена
    if user.email_verification_sent_at:
        expires = user.email_verification_sent_at.replace(tzinfo=timezone.utc) + timedelta(hours=EMAIL_VERIFY_EXPIRY_HOURS)
        if datetime.now(timezone.utc) > expires:
            return None

    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------

async def login(db: AsyncSession, email: str, password: str) -> Optional[User]:
    """
    Проверяет учётные данные.
    Возвращает User при успехе.
    Возвращает None если пароль неверен.
    Raises ValueError("password_not_set") если пользователь мигрирован (password_hash IS NULL).
    Raises ValueError("not_found") если email не найден.
    """
    result = await db.execute(
        select(User)
        .where(User.email == email)
        .order_by(User.id.desc())
        .limit(1)
    )
    user = result.scalars().first()
    if not user:
        raise ValueError("not_found")

    if not user.password_hash:
        raise ValueError("password_not_set")

    if not user.check_password(password):
        return None

    return user


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------

async def create_session(db: AsyncSession, user: User) -> str:
    """Создаёт сессию в БД, возвращает session_token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    session = UserSession(user_id=user.id, session_token=token, expires_at=expires_at)
    db.add(session)
    await db.commit()
    return token


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency: читает cookie user_session, валидирует в БД.
    Бросает 401 если не авторизован.
    """
    session_token = request.cookies.get("user_session")
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await db.execute(
        select(UserSession).where(
            UserSession.session_token == session_token,
            UserSession.expires_at > datetime.now(timezone.utc),
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = await db.get(User, session.user_id)
    if not user or user.is_blocked:
        raise HTTPException(status_code=401, detail="User not found or blocked")

    return user


async def get_optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Как get_current_user, но возвращает None вместо 401."""
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# Сброс пароля
# ---------------------------------------------------------------------------

async def prepare_password_reset_email(db: AsyncSession, email: str) -> Optional[tuple[str, str, str]]:
    """
    Готовит письмо со ссылкой для сброса пароля.
    Возвращает (to, subject, html_body) или None если email не найден/не подходит.
    """
    result = await db.execute(
        select(User)
        .where(User.email == email)
        .order_by(User.id.desc())
        .limit(1)
    )
    user = result.scalars().first()
    if not user or not user.password_hash:
        return None  # для мигрированных — отдельный флоу (setup)

    token = secrets.token_urlsafe(32)
    user.password_reset_token = token
    user.password_reset_sent_at = datetime.now(timezone.utc)
    await db.commit()

    reset_url = f"{settings.SITE_URL}/auth/password-reset/confirm?token={token}"
    html = _render("reset_password.html", reset_url=reset_url)
    return (email, "Сброс пароля", html)


async def reset_password(db: AsyncSession, token: str, new_password: str) -> bool:
    """
    Устанавливает новый пароль по токену сброса.
    Возвращает True при успехе, False если токен не найден или истёк.
    """
    result = await db.execute(
        select(User).where(User.password_reset_token == token)
    )
    user = result.scalar_one_or_none()
    if not user:
        return False

    if user.password_reset_sent_at:
        expires = user.password_reset_sent_at.replace(tzinfo=timezone.utc) + timedelta(hours=PASSWORD_RESET_EXPIRY_HOURS)
        if datetime.now(timezone.utc) > expires:
            return False

    user.set_password(new_password)
    user.password_reset_token = None
    user.password_reset_sent_at = None
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Установка пароля (для мигрированных бот-пользователей)
# ---------------------------------------------------------------------------

async def prepare_password_setup_email(db: AsyncSession, email: str) -> Optional[tuple[str, str, str]]:
    """
    Готовит письмо мигрированному пользователю (password_hash IS NULL)
    со ссылкой для установки пароля.
    """
    result = await db.execute(
        select(User)
        .where(User.email == email)
        .order_by(User.id.desc())
        .limit(1)
    )
    user = result.scalars().first()
    # Отправляем только если пользователь найден и пароль не установлен
    if not user or user.password_hash:
        return None

    token = secrets.token_urlsafe(32)
    user.password_reset_token = token
    user.password_reset_sent_at = datetime.now(timezone.utc)
    await db.commit()

    setup_url = f"{settings.SITE_URL}/auth/password-setup/confirm?token={token}"
    html = _render("set_password_migration.html", setup_url=setup_url)
    return (email, "Установите пароль для входа на сайт", html)


async def confirm_password_setup(db: AsyncSession, token: str, new_password: str) -> bool:
    """
    Устанавливает пароль мигрированному пользователю.
    Использует тот же поле password_reset_token, но с более длинным сроком.
    Возвращает True при успехе.
    """
    result = await db.execute(
        select(User).where(User.password_reset_token == token)
    )
    user = result.scalar_one_or_none()
    if not user:
        return False

    if user.password_reset_sent_at:
        expires = user.password_reset_sent_at.replace(tzinfo=timezone.utc) + timedelta(days=PASSWORD_SETUP_EXPIRY_DAYS)
        if datetime.now(timezone.utc) > expires:
            return False

    user.set_password(new_password)
    user.email_verified = True  # считаем email подтверждённым после установки пароля
    user.password_reset_token = None
    user.password_reset_sent_at = None
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Повторная отправка верификации
# ---------------------------------------------------------------------------

async def resend_verification(db: AsyncSession, user: User) -> Optional[tuple[str, str, str]]:
    """
    Совместимость со старым названием: возвращает данные письма (или None).
    """
    return await prepare_email_verification(db, user)


# ---------------------------------------------------------------------------
# Одноразовый вход из Telegram-бота
# ---------------------------------------------------------------------------

async def create_login_token(db: AsyncSession, user: User) -> str:
    """
    Выдаёт одноразовый токен входа на сайт. Нужен покупателям из бота,
    у которых нет email (письмо «установите пароль» им не отправить).
    Токен живёт LOGIN_TOKEN_EXPIRY_MINUTES и сгорает при первом использовании.
    """
    token = secrets.token_urlsafe(32)
    user.login_token = token
    user.login_token_sent_at = datetime.now(timezone.utc)
    await db.commit()
    return token


async def consume_login_token(db: AsyncSession, token: str) -> Optional[User]:
    """
    Меняет одноразовый токен на пользователя и гасит токен.
    None, если токен не найден, просрочен или пользователь заблокирован.
    """
    result = await db.execute(select(User).where(User.login_token == token))
    user = result.scalar_one_or_none()
    if not user:
        return None

    sent_at = user.login_token_sent_at
    expired = True
    if sent_at:
        expires = sent_at.replace(tzinfo=timezone.utc) + timedelta(minutes=LOGIN_TOKEN_EXPIRY_MINUTES)
        expired = datetime.now(timezone.utc) > expires

    # Токен одноразовый: гасим независимо от того, годен он или уже протух
    user.login_token = None
    user.login_token_sent_at = None
    await db.commit()

    if expired or user.is_blocked:
        return None

    await db.refresh(user)
    return user
