"""
Rate limiting (fixed window) для чувствительных эндпоинтов: логин, регистрация,
сброс пароля, платежи, waitlist.

Хранилище: Redis, если доступен (лимит общий для всех воркеров uvicorn).
Если Redis не установлен или упал — счётчики в памяти процесса: каждый воркер
считает сам, т.е. фактический лимит = limit * число воркеров, но защита остаётся.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

# После ошибки Redis не дёргаем его заново какое-то время — иначе каждый запрос
# ждал бы таймаут подключения.
REDIS_RETRY_AFTER_SECONDS = 60
REDIS_TIMEOUT_SECONDS = 0.3


@dataclass(frozen=True)
class Limit:
    scope: str
    limit: int
    window: int  # секунды


# --- Лимиты ---------------------------------------------------------------
LOGIN_IP = Limit("login_ip", 10, 5 * 60)
LOGIN_EMAIL = Limit("login_email", 5, 5 * 60)
REGISTER_IP = Limit("register_ip", 5, 60 * 60)
PASSWORD_EMAIL = Limit("password_email", 3, 60 * 60)
PASSWORD_IP = Limit("password_ip", 10, 60 * 60)
RESEND_VERIFICATION_USER = Limit("resend_verification", 3, 60 * 60)
LOGIN_TOKEN_IP = Limit("login_token_ip", 20, 10 * 60)
ADMIN_LOGIN_IP = Limit("admin_login_ip", 5, 15 * 60)
PAYMENT_USER = Limit("payment_user", 5, 10 * 60)
WAITLIST_IP = Limit("waitlist_ip", 5, 60 * 60)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Rate limit exceeded, retry after {retry_after}s")
        self.retry_after = retry_after


class MemoryBackend:
    """Счётчики в памяти процесса с ограничением размера (защита от перебора IP)."""

    MAX_KEYS = 50_000

    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}  # key -> (count, expires_at)

    def _evict(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._data.items() if exp <= now]
        for k in expired:
            del self._data[k]
        if len(self._data) >= self.MAX_KEYS:
            # Всё ещё переполнено живыми ключами: выкидываем самые старые окна.
            for k, _ in sorted(self._data.items(), key=lambda kv: kv[1][1])[
                : len(self._data) // 2
            ]:
                del self._data[k]

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        now = time.monotonic()
        count, expires_at = self._data.get(key, (0, 0.0))
        if expires_at <= now:
            if len(self._data) >= self.MAX_KEYS:
                self._evict(now)
            count, expires_at = 0, now + window
        count += 1
        self._data[key] = (count, expires_at)
        return count, max(1, math.ceil(expires_at - now))

    def clear(self) -> None:
        self._data.clear()


class RedisBackend:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis_async

        self._client = redis_async.from_url(
            url,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        )

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        # Без EXPIRE NX: он есть только в Redis 7+, а версия на сервере неизвестна.
        pipe = self._client.pipeline(transaction=True)
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if ttl is None or ttl < 0:  # новый ключ (или потерял TTL) — ставим окно
            await self._client.expire(key, window)
            ttl = window
        return int(count), int(ttl)


class HybridBackend:
    """Redis с откатом в память, если Redis недоступен."""

    def __init__(self, redis_url: str | None) -> None:
        self.memory = MemoryBackend()
        self._redis: RedisBackend | None = None
        self._redis_url = redis_url
        self._redis_down_until = 0.0

    def _get_redis(self) -> RedisBackend | None:
        if not self._redis_url or time.monotonic() < self._redis_down_until:
            return None
        if self._redis is None:
            try:
                self._redis = RedisBackend(self._redis_url)
            except Exception as e:  # пакет/URL битые — живём на памяти
                logger.warning("Rate limit: Redis init failed (%s), using memory", e)
                self._redis_down_until = float("inf")
                return None
        return self._redis

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        redis_backend = self._get_redis()
        if redis_backend is not None:
            try:
                return await redis_backend.hit(key, window)
            except Exception as e:
                logger.warning(
                    "Rate limit: Redis unavailable (%s), fallback to memory for %ss",
                    e,
                    REDIS_RETRY_AFTER_SECONDS,
                )
                self._redis_down_until = time.monotonic() + REDIS_RETRY_AFTER_SECONDS
        return await self.memory.hit(key, window)


backend: HybridBackend | MemoryBackend = HybridBackend(settings.REDIS_URL)


def client_ip(request: Request) -> str:
    # uvicorn запущен с --proxy-headers, client.host = реальный IP от nginx.
    return request.client.host if request.client else "unknown"


async def check(limit: Limit, identity: str | int | None) -> None:
    """Засчитывает попытку; бросает RateLimitExceeded при превышении."""
    if not settings.RATE_LIMIT_ENABLED or identity is None or identity == "":
        return
    key = f"rl:{limit.scope}:{str(identity).strip().lower()}"
    try:
        count, ttl = await backend.hit(key, limit.window)
    except Exception as e:  # лимитер никогда не должен ронять запрос
        logger.error("Rate limit backend error: %s", e)
        return
    if count > limit.limit:
        raise RateLimitExceeded(ttl)


async def enforce(limit: Limit, identity: str | int | None) -> None:
    """Как check(), но сразу отдаёт HTTP 429 (для JSON API)."""
    try:
        await check(limit, identity)
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Попробуйте позже.",
            headers={"Retry-After": str(e.retry_after)},
        )
