from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os
import jwt
from datetime import datetime

from app.api.admin import get_current_admin
from app.database import get_db
from app.models.user import User
from app.models.material import Material
from app.config import settings

router = APIRouter(prefix="/api/v1/materials", tags=["materials"])


@router.get("/{material_id}/download")
async def download_material(
        material_id: int,
        token: str,
        db: AsyncSession = Depends(get_db)
):
    """Скачивание материала с проверкой доступа"""
    try:
        # Проверяем JWT токен
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"]
        )

        user_id = payload.get("user_id")

        # Проверяем доступ пользователя
        user = await db.get(User, user_id)
        if not user or not user.has_access:
            raise HTTPException(status_code=403, detail="Access denied")

        # Получаем материал
        material = await db.get(Material, material_id)
        if not material:
            raise HTTPException(status_code=404, detail="Material not found")

        # Увеличиваем счетчик скачиваний
        material.downloads_count += 1
        await db.commit()

        # Путь к файлу
        file_path = os.path.join("uploads", "materials", material.file_name)

        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")

        return FileResponse(
            path=file_path,
            filename=material.original_name,
            media_type=material.file_type
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.get("/lesson/{lesson_id}")
async def get_lesson_materials(
        lesson_id: int,
        db: AsyncSession = Depends(get_db)
):
    """Получить все материалы урока"""
    result = await db.execute(
        select(Material).where(Material.lesson_id == lesson_id)
    )
    materials = result.scalars().all()
    return materials


@router.delete("/{material_id}")
async def delete_material(
        material_id: int,
        lesson_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    """Удалить материал"""
    material = await db.get(Material, material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    # Удаляем файл
    file_path = os.path.join("uploads", "materials", material.file_name)
    if os.path.exists(file_path):
        os.remove(file_path)

    # Удаляем запись из БД
    await db.delete(material)
    await db.commit()

    return {"status": "ok"}