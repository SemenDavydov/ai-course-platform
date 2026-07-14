"""
Тесты Этапа 1.6: регистрация, вход, верификация email, сброс пароля,
установка пароля для мигрированного пользователя.
"""
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.user_session import UserSession


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def verified_user(db_session: AsyncSession) -> User:
    """Верифицированный веб-пользователь с паролем."""
    user = User(
        email="verified@test.com",
        email_verified=True,
        registration_source="web",
        accepted_offer=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def migrated_user(db_session: AsyncSession) -> User:
    """Бот-пользователь без пароля (мигрированный), но с email."""
    user = User(
        telegram_id=999888777,
        email="migrated@test.com",
        email_verified=True,
        registration_source="bot_migrated",
        accepted_offer=False,
        # password_hash = NULL — пароль не установлен
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def session_cookie(db_session: AsyncSession, verified_user: User) -> str:
    """Создаёт сессию в БД и возвращает токен."""
    token = "test_valid_session_token_xyz"
    session = UserSession(
        user_id=verified_user.id,
        session_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(session)
    await db_session.commit()
    return token


# ---------------------------------------------------------------------------
# Регистрация
# ---------------------------------------------------------------------------

@patch("app.tasks.send_email_task.delay")
async def test_register_success(mock_delay, client: AsyncClient, db_session: AsyncSession):
    """Регистрация нового пользователя: 201 + cookie выставлен."""
    response = await client.post("/auth/register", json={
        "email": "newuser@test.com",
        "password": "securepass123",
        "accepted_offer": True,
    })
    assert response.status_code == 201
    assert "user_session" in response.cookies

    # email_verified = False до подтверждения (для доступа к курсу не требуется)
    result = await db_session.execute(select(User).where(User.email == "newuser@test.com"))
    user = result.scalar_one()
    assert user.email_verified is False
    assert user.registration_source == "web"
    assert user.accepted_offer is True

    # Задача отправки письма поставлена в очередь
    mock_delay.assert_called_once()


@patch("app.tasks.send_email_task.delay")
async def test_register_duplicate_email(mock_delay, client: AsyncClient, verified_user: User):
    """Регистрация с уже занятым email → 409."""
    response = await client.post("/auth/register", json={
        "email": verified_user.email,
        "password": "password123",
        "accepted_offer": True,
    })
    assert response.status_code == 409


@patch("app.tasks.send_email_task.delay")
async def test_register_no_offer(mock_delay, client: AsyncClient):
    """Регистрация без принятия оферты → 422."""
    response = await client.post("/auth/register", json={
        "email": "nooffer@test.com",
        "password": "password123",
        "accepted_offer": False,
    })
    assert response.status_code == 422


@patch("app.tasks.send_email_task.delay")
async def test_register_short_password(mock_delay, client: AsyncClient):
    """Пароль короче 8 символов → 422."""
    response = await client.post("/auth/register", json={
        "email": "short@test.com",
        "password": "abc",
        "accepted_offer": True,
    })
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Вход / выход
# ---------------------------------------------------------------------------

async def test_login_success(client: AsyncClient, verified_user: User):
    """Корректный логин → 200 + cookie."""
    response = await client.post("/auth/login", json={
        "email": verified_user.email,
        "password": "password123",
    })
    assert response.status_code == 200
    assert "user_session" in response.cookies


async def test_login_wrong_password(client: AsyncClient, verified_user: User):
    """Неверный пароль → 401."""
    response = await client.post("/auth/login", json={
        "email": verified_user.email,
        "password": "wrongpassword",
    })
    assert response.status_code == 401


async def test_login_unknown_email(client: AsyncClient):
    """Несуществующий email → 401."""
    response = await client.post("/auth/login", json={
        "email": "nobody@test.com",
        "password": "password123",
    })
    assert response.status_code == 401


@patch("app.tasks.send_email_task.delay")
async def test_login_migrated_no_password(mock_delay, client: AsyncClient, migrated_user: User):
    """Мигрированный пользователь без пароля → 409 + X-Auth-Action: setup_password."""
    response = await client.post("/auth/login", json={
        "email": migrated_user.email,
        "password": "anything",
    })
    assert response.status_code == 409
    assert response.headers.get("X-Auth-Action") == "setup_password"


async def test_logout(client: AsyncClient, session_cookie: str):
    """Выход: cookie удаляется."""
    response = await client.post(
        "/auth/logout",
        cookies={"user_session": session_cookie},
    )
    assert response.status_code in (302, 303)
    # После логаута cookie сброшен (max_age=0 / Set-Cookie: user_session=""; ...)
    assert response.cookies.get("user_session", "") in ("", None)


# ---------------------------------------------------------------------------
# Верификация email
# ---------------------------------------------------------------------------

@patch("app.tasks.send_email_task.delay")
async def test_verify_email_valid_token(mock_delay, client: AsyncClient, db_session: AsyncSession):
    """Переход по верному токену → redirect, email_verified=True в БД."""
    # Создаём пользователя с токеном
    token = "valid_verify_token_abc"
    user = User(
        email="toverify@test.com",
        email_verified=False,
        email_verification_token=token,
        email_verification_sent_at=datetime.now(timezone.utc),
        registration_source="web",
        accepted_offer=True,
    )
    user.set_password("password123")
    db_session.add(user)
    await db_session.commit()

    response = await client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert response.status_code == 302
    assert "auth_error" not in response.headers["location"]

    await db_session.refresh(user)
    assert user.email_verified is True
    assert user.email_verification_token is None


async def test_verify_email_invalid_token(client: AsyncClient):
    """Неверный токен → редирект с auth_error."""
    response = await client.get("/auth/verify-email?token=bogus_token", follow_redirects=False)
    assert response.status_code == 302
    assert "auth_error" in response.headers["location"]


# ---------------------------------------------------------------------------
# Сброс пароля
# ---------------------------------------------------------------------------

@patch("app.tasks.send_email_task.delay")
async def test_password_reset_request(mock_delay, client: AsyncClient, verified_user: User):
    """Запрос сброса пароля → 200, письмо поставлено в очередь."""
    response = await client.post("/auth/password-reset/request", json={
        "email": verified_user.email,
    })
    assert response.status_code == 200
    mock_delay.assert_called_once()


@patch("app.tasks.send_email_task.delay")
async def test_password_reset_request_unknown_email(mock_delay, client: AsyncClient):
    """Запрос для несуществующего email → 200 (не раскрываем наличие аккаунта)."""
    response = await client.post("/auth/password-reset/request", json={
        "email": "nobody@test.com",
    })
    assert response.status_code == 200
    mock_delay.assert_not_called()


@patch("app.tasks.send_email_task.delay")
async def test_password_reset_confirm(mock_delay, client: AsyncClient, db_session: AsyncSession, verified_user: User):
    """Подтверждение сброса пароля с корректным токеном → 200, пароль изменён."""
    # Проставляем токен напрямую
    token = "reset_token_valid_xyz"
    verified_user.password_reset_token = token
    verified_user.password_reset_sent_at = datetime.now(timezone.utc)
    db_session.add(verified_user)
    await db_session.commit()

    response = await client.post("/auth/password-reset/confirm", json={
        "token": token,
        "password": "newpassword123",
    })
    assert response.status_code == 200

    await db_session.refresh(verified_user)
    assert verified_user.check_password("newpassword123")
    assert verified_user.password_reset_token is None


async def test_password_reset_confirm_invalid_token(client: AsyncClient):
    """Неверный токен сброса → 400."""
    response = await client.post("/auth/password-reset/confirm", json={
        "token": "invalid_token",
        "password": "newpassword123",
    })
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Установка пароля для мигрированного пользователя
# ---------------------------------------------------------------------------

@patch("app.tasks.send_email_task.delay")
async def test_password_setup_request(mock_delay, client: AsyncClient, migrated_user: User):
    """Запрос установки пароля для мигрированного → 200, письмо отправлено."""
    response = await client.post("/auth/password-setup/request", json={
        "email": migrated_user.email,
    })
    assert response.status_code == 200
    mock_delay.assert_called_once()


@patch("app.tasks.send_email_task.delay")
async def test_password_setup_request_ignores_web_user(mock_delay, client: AsyncClient, verified_user: User):
    """Запрос setup для пользователя с уже установленным паролем → 200, письмо НЕ отправляется."""
    response = await client.post("/auth/password-setup/request", json={
        "email": verified_user.email,
    })
    assert response.status_code == 200
    mock_delay.assert_not_called()


@patch("app.tasks.send_email_task.delay")
async def test_password_setup_confirm(mock_delay, client: AsyncClient, db_session: AsyncSession, migrated_user: User):
    """Мигрированный пользователь устанавливает пароль → 200, может войти."""
    token = "setup_token_migrated_xyz"
    migrated_user.password_reset_token = token
    migrated_user.password_reset_sent_at = datetime.now(timezone.utc)
    db_session.add(migrated_user)
    await db_session.commit()

    response = await client.post("/auth/password-setup/confirm", json={
        "token": token,
        "password": "newpassword123",
    })
    assert response.status_code == 200

    await db_session.refresh(migrated_user)
    assert migrated_user.check_password("newpassword123")
    assert migrated_user.email_verified is True  # автоматически верифицируем
    assert migrated_user.password_reset_token is None


@patch("app.tasks.send_email_task.delay")
async def test_migrated_user_full_flow(mock_delay, client: AsyncClient, db_session: AsyncSession, migrated_user: User):
    """Полный сценарий: мигрированный пытается войти → setup → входит."""
    # 1. Попытка входа без пароля → 409
    login_resp = await client.post("/auth/login", json={
        "email": migrated_user.email,
        "password": "anything",
    })
    assert login_resp.status_code == 409

    # 2. Запрос письма для установки пароля
    await client.post("/auth/password-setup/request", json={"email": migrated_user.email})
    await db_session.refresh(migrated_user)
    token = migrated_user.password_reset_token
    assert token is not None

    # 3. Установка пароля по токену
    setup_resp = await client.post("/auth/password-setup/confirm", json={
        "token": token,
        "password": "mypassword123",
    })
    assert setup_resp.status_code == 200

    # 4. Успешный вход
    login_resp2 = await client.post("/auth/login", json={
        "email": migrated_user.email,
        "password": "mypassword123",
    })
    assert login_resp2.status_code == 200
    assert "user_session" in login_resp2.cookies
