from typing import Optional

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
from app.models.course import Lesson
from app.services.auth import get_optional_user
from app.services.access import user_has_course
from app.config import settings

router = APIRouter(prefix="/api/v1/materials", tags=["materials"])


async def _resolve_material_user(
    request: Request,
    token: Optional[str],
    db: AsyncSession,
) -> User:
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=403, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=403, detail="Invalid token")
        user = await db.get(User, payload.get("user_id"))
    else:
        user = await get_optional_user(request, db)

    if not user or not user.has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    return user


@router.get("/{material_id}/download")
async def download_material(
        material_id: int,
        request: Request,
        token: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
):
    user = await _resolve_material_user(request, token, db)

    material = await db.get(Material, material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    lesson = await db.get(Lesson, material.lesson_id)
    if lesson and not await user_has_course(db, user, lesson.course_id):
        raise HTTPException(status_code=403, detail="Access denied")

    file_path = os.path.join("uploads", "materials", material.file_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    material.downloads_count += 1
    await db.commit()

    return FileResponse(
        path=file_path,
        filename=material.original_name,
        media_type=material.file_type
    )


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