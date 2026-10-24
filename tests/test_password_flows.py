"""Сброс/установка пароля: ссылки из писем открываются, аккаунты из бота получают письмо,
email без учёта регистра, поиск в админке не падает."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_session import AdminSession
from app.models.user import User


def _token_from(html: str, path: str) -> str:
    marker = f"{path}?token="
    start = html.index(marker) + len(marker)
    end = start
    while end < len(html) and html[end] not in '"\'<& ':
        end += 1
    return html[start:end]


@pytest.mark.asyncio
async def test_reset_for_bot_user_without_password_sends_setup_email(client, db_session: AsyncSession):
    """Раньше: аккаунт без пароля (из бота) — письмо молча не уходило."""
    u = User(email="LisaDav@Mail.ru", telegram_id=777, registration_source="bot_migrated", has_access=True)
    db_session.add(u)
    await db_session.commit()

    with patch("app.api.auth.enqueue_email", return_value=True) as send:
        r = await client.post("/auth/password-reset/request", json={"email": "lisadav@mail.ru"})
    assert r.status_code == 200
    send.assert_called_once()
    to, subject, html = send.call_args.args
    assert to == "LisaDav@Mail.ru"           # на адрес из базы, регистр не важен
    assert "Установите пароль" in subject
    assert "/auth/password-setup/confirm?token=" in html


@pytest.mark.asyncio
async def test_email_links_open_a_page_not_405(client):
    for path in ("/auth/password-reset/confirm", "/auth/password-setup/confirm"):
        r = await client.get(f"{path}?token=abc")
        assert r.status_code == 200, path
        assert 'id="password-form"' in r.text
    r = await client.get("/auth/password-reset/confirm")
    assert "Ссылка неполная" in r.text


@pytest.mark.asyncio
async def test_full_setup_flow_logs_in(client, db_session):
    u = User(email="bot@example.com", telegram_id=888, registration_source="bot_migrated", has_access=True)
    db_session.add(u)
    await db_session.commit()

    with patch("app.api.auth.enqueue_email", return_value=True) as send:
        await client.post("/auth/password-reset/request", json={"email": "BOT@example.com"})
    token = _token_from(send.call_args.args[2], "/auth/password-setup/confirm")

    page = await client.get(f"/auth/password-setup/confirm?token={token}")  # GET токен не тратит
    assert page.status_code == 200
    r = await client.post("/auth/password-setup/confirm", json={"token": token, "password": "newpass123"})
    assert r.status_code == 200 and r.json()["redirect"] == "/cabinet/lessons"
    assert "user_session" in r.cookies

    client.cookies.clear()
    r = await client.post("/auth/login", json={"email": "Bot@Example.com", "password": "newpass123"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_full_reset_flow_logs_in(client, db_session):
    u = User(email="web@example.com", registration_source="web")
    u.set_password("oldpass123")
    db_session.add(u)
    await db_session.commit()

    with patch("app.api.auth.enqueue_email", return_value=True) as send:
        await client.post("/auth/password-reset/request", json={"email": "web@example.com"})
    assert send.call_args.args[1] == "Сброс пароля"
    token = _token_from(send.call_args.args[2], "/auth/password-reset/confirm")
    r = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "brandnew123"})
    assert r.status_code == 200 and "user_session" in r.cookies
    # токен одноразовый
    r2 = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "another123"})
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_register_is_case_insensitive(client, db_session):
    u = User(email="Taken@Example.com", registration_source="web")
    u.set_password("password123")
    db_session.add(u)
    await db_session.commit()
    r = await client.post("/auth/register", json={
        "email": "taken@example.com", "password": "password123", "accepted_offer": True,
    })
    assert r.status_code == 409

    with patch("app.api.auth.enqueue_email", return_value=True):
        r = await client.post("/auth/register", json={
            "email": "New.User@Example.com", "password": "password123", "accepted_offer": True,
        })
    assert r.status_code == 201
    created = await db_session.get(User, r.json()["user_id"])
    assert created.email == "new.user@example.com"


@pytest.mark.asyncio
async def test_admin_user_search_no_500(client, db_session):
    admin = User(username="boss", email="boss@example.com", role="admin", is_admin=True, registration_source="web")
    target = User(email="lisadav3100@mail.ru", telegram_id=123456, registration_source="bot_migrated")
    db_session.add_all([admin, target])
    await db_session.flush()
    db_session.add(AdminSession(user_id=admin.id, session_token="adm",
                                expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await db_session.commit()
    client.cookies.set("admin_session", "adm")

    for q in ("lisadav3100@mail.ru", "LISADAV", "123456", "nobody"):
        r = await client.get("/admin/users", params={"search": q})
        assert r.status_code == 200, q
    assert "lisadav3100@mail.ru" in (await client.get("/admin/users", params={"search": "LISADAV"})).text
    assert "lisadav3100@mail.ru" in (await client.get("/admin/users", params={"search": "1234"})).text
