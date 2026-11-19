"""Поднять схему и минимальные данные в локальной БД для скана ZAP.

Не для прода: работает только с БД, в имени которой есть "test", иначе
отказывается запускаться. Схема создаётся через metadata.create_all, как в
tests/conftest.py, — alembic тут не нужен, база одноразовая.

Запуск:
  $env:ZAP_DB_URL = "postgresql+asyncpg://postgres:ПАРОЛЬ@localhost:5432/aicourse_test"
  .venv\\Scripts\\python.exe scripts\\zap_seed_local.py
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_URL = os.environ.get("ZAP_DB_URL", "")
if "test" not in DB_URL:
    sys.exit("ZAP_DB_URL должен указывать на тестовую БД (в имени ожидается 'test')")

# app.database читает URL из settings на импорте, поэтому подменяем заранее.
os.environ["DATABASE_URL"] = DB_URL

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

importlib.import_module("app.models")
from app.database import Base  # noqa: E402
from app.models.course import Course, Lesson, Tariff  # noqa: E402


async def main() -> None:
    engine = create_async_engine(DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as db:
        course = Course(
            slug="ai-story",
            title="AI STORY",
            description="Курс для скана ZAP",
            price=8990,
            is_published=True,
            sort_order=1,
        )
        db.add(course)
        await db.flush()
        db.add(
            Tariff(
                course_id=course.id,
                slug="self",
                name="Самостоятельный",
                price=8990,
                is_active=True,
                sort_order=1,
            )
        )
        db.add(
            Lesson(
                course_id=course.id,
                title="Урок 1",
                description="Для скана",
                order=1,
            )
        )
        await db.commit()

    await engine.dispose()
    print("schema + seed OK:", DB_URL.rsplit("/", 1)[-1])


if __name__ == "__main__":
    asyncio.run(main())
