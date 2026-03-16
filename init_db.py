import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.database import Base
from app.config import settings


async def init_database():
    """Создает базу данных и таблицы"""

    # Подключаемся к стандартной postgres базе
    default_url = settings.DATABASE_URL.replace("aicourse", "postgres")
    engine = create_async_engine(default_url)

    async with engine.connect() as conn:
        await conn.execute(text("COMMIT"))
        # Создаем базу если её нет
        try:
            await conn.execute(text("CREATE DATABASE aicourse"))
            print("База данных aicourse создана")
        except:
            print("База данных aicourse уже существует")

    # Создаем таблицы
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("Таблицы созданы")


if __name__ == "__main__":
    asyncio.run(init_database())