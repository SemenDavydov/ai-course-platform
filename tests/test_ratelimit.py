import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services import ratelimit


async def _make_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, registration_source="web", accepted_offer=True)
    user.set_password("password123")
    db.add(user)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_login_limited_per_ip(client: AsyncClient):
    limit = ratelimit.LOGIN_IP.limit
    # Разные email, чтобы сработал именно лимит по IP.
    for i in range(limit):
        r = await client.post(
            "/auth/login", json={"email": f"u{i}@example.com", "password": "x" * 8}
        )
        assert r.status_code == 401
    r = await client.post(
        "/auth/login", json={"email": "last@example.com", "password": "x" * 8}
    )
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_login_limited_per_email(client: AsyncClient, db_session: AsyncSession):
    await _make_user(db_session, "victim@example.com")
    limit = ratelimit.LOGIN_EMAIL.limit
    for _ in range(limit):
        r = await client.post(
            "/auth/login", json={"email": "victim@example.com", "password": "wrong-pass"}
        )
        assert r.status_code == 401
    # Даже правильный пароль не проверяется, пока email заблокирован.
    r = await client.post(
        "/auth/login", json={"email": "victim@example.com", "password": "password123"}
    )
    assert r.status_code == 429
    # Email нормализуется: регистр не обходит лимит.
    r = await client.post(
        "/auth/login", json={"email": "VICTIM@example.com", "password": "password123"}
    )
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_password_reset_limited_per_email(client: AsyncClient):
    for _ in range(ratelimit.PASSWORD_EMAIL.limit):
        r = await client.post(
            "/auth/password-reset/request", json={"email": "spam@example.com"}
        )
        assert r.status_code == 200
    r = await client.post("/auth/password-reset/request", json={"email": "spam@example.com"})
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_admin_login_limited(client: AsyncClient):
    for _ in range(ratelimit.ADMIN_LOGIN_IP.limit):
        r = await client.post("/admin/login", data={"username": "a", "password": "b"})
        assert r.status_code == 200
    r = await client.post("/admin/login", data={"username": "a", "password": "b"})
    assert r.status_code == 429
    assert "Слишком много попыток" in r.text


@pytest.mark.asyncio
async def test_limiter_fails_open_when_backend_broken(client: AsyncClient, monkeypatch):
    class Broken:
        async def hit(self, key, window):
            raise ConnectionError("redis down")

    monkeypatch.setattr(ratelimit, "backend", Broken())
    for _ in range(ratelimit.LOGIN_IP.limit + 5):
        r = await client.post(
            "/auth/login", json={"email": "x@example.com", "password": "x" * 8}
        )
        assert r.status_code == 401  # не 429 и не 500


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_memory_when_redis_down():
    hybrid = ratelimit.HybridBackend("redis://127.0.0.1:1/0")  # заведомо закрытый порт
    limit = ratelimit.Limit("t", 2, 60)
    counts = [(await hybrid.hit("rl:t:k", limit.window))[0] for _ in range(3)]
    assert counts == [1, 2, 3]  # считает в памяти, а не падает


@pytest.mark.asyncio
async def test_memory_backend_is_bounded(monkeypatch):
    mem = ratelimit.MemoryBackend()
    monkeypatch.setattr(ratelimit.MemoryBackend, "MAX_KEYS", 100)
    for i in range(1000):
        await mem.hit(f"rl:x:{i}", 60)
    assert len(mem._data) <= 100
