# tests/conftest.py
import pytest
import asyncio
import sys
import os
from typing import AsyncGenerator
from unittest.mock import MagicMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

# Добавляем путь к проекту в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import app
from app.api.webhooks import verify_yookassa_source
from app.database import Base, get_db
from app.config import settings

# В тестах письма отправляются синхронно, иначе фоновый поток гонялся бы
# с проверками моков.
settings.EMAIL_BACKGROUND = False

# ВАЖНО: форсируем импорт всех моделей до create_all().
# Иначе Base.metadata может быть неполной, и таблицы не создадутся.
import importlib
importlib.import_module("app.models")

# Тестовая БД
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:Rtdbykfd13@localhost:5432/aicourse_test"

engine_test = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
async_session_maker = async_sessionmaker(engine_test, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db

# Тестовый клиент приходит не с IP ЮKassa. Саму проверку IP покрывает
# отдельный тест, который временно снимает этот override.
app.dependency_overrides[verify_yookassa_source] = lambda: None


@pytest.fixture
def yookassa_api():
    """
    Мокает подтверждение платежа в API ЮKassa (webhook сверяется с ним, а не с телом запроса).
    По умолчанию — успешный платёж на 9990.00; переопределить: yookassa_api.set_payment(...).
    """
    with patch("app.api.webhooks.YooPayment.find_one") as mock:
        def set_payment(status: str = "succeeded", amount: str = "9990.00"):
            remote = MagicMock()
            remote.status = status
            remote.amount.value = amount
            remote.metadata = {}
            mock.return_value = remote
            return remote

        set_payment()
        mock.set_payment = set_payment
        yield mock


@pytest.fixture(autouse=True)
async def setup_database():
    # Создаем тестовую БД если её нет
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    # Подключаемся к стандартной postgres базе для создания тестовой
    temp_engine = create_async_engine("postgresql+asyncpg://postgres:Rtdbykfd13@localhost:5432/postgres")
    async with temp_engine.connect() as conn:
        await conn.execute(text("COMMIT"))  # Нужно для создания БД вне транзакции
        try:
            await conn.execute(text("CREATE DATABASE aicourse_test"))
        except:
            pass  # База уже существует

    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client(setup_database) -> AsyncGenerator:
    """Depends on setup_database so schema exists before any request (avoids async fixture races)."""
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def db_session(setup_database) -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
        await session.rollback()