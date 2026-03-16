# tests/conftest.py
import pytest
import asyncio
import sys
import os
from typing import AsyncGenerator
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

# Добавляем путь к проекту в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import app
from app.database import Base, get_db
from app.config import settings

# Тестовая БД
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:password@localhost:5432/aicourse_test"

engine_test = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
async_session_maker = async_sessionmaker(engine_test, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
async def setup_database():
    # Создаем тестовую БД если её нет
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    # Подключаемся к стандартной postgres базе для создания тестовой
    temp_engine = create_async_engine("postgresql+asyncpg://postgres:password@localhost:5432/postgres")
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
async def client() -> AsyncGenerator:
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
        await session.rollback()