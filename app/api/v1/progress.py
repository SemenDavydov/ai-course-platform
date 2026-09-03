"""
Progress API: отслеживание прогресса просмотра уроков.
Prefix: /api/v1/progress
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.database import get_db
from app.models.lesson_progress import LessonProgress
from app.models.course import Lesson
from app.services.auth import get_current_user
from app.services.access import user_has_course

router = APIRouter(prefix="/api/v1/progress", tags=["progress"])

COMPLETION_THRESHOLD = 90  # % просмотра → урок считается пройденным


class ProgressUpdate(BaseModel):
    lesson_id: int
    percent_watched: int = Field(..., ge=0, le=100)


class ProgressItem(BaseModel):
    lesson_id: int
    percent_watched: int
    is_completed: bool

    class Config:
        from_attributes = True


@router.post("", response_model=ProgressItem)
async def update_progress(
    body: ProgressUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(request, db)

    if not user.has_access:
        raise HTTPException(status_code=403, detail="Нет доступа к курсу")

    lesson_result = await db.execute(select(Lesson).where(Lesson.id == body.lesson_id))
    lesson = lesson_result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Урок не найден")

    if not await user_has_course(db, user, lesson.course_id):
        raise HTTPException(status_code=403, detail="Нет доступа к этому курсу")

    # Ищем существующую запись
    result = await db.execute(
        select(LessonProgress).where(
            and_(
                LessonProgress.user_id == user.id,
                LessonProgress.lesson_id == body.lesson_id,
            )
        )
    )
    progress = result.scalar_one_or_none()

    if progress:
        # Обновляем только если новое значение больше
        if body.percent_watched > progress.percent_watched:
            progress.percent_watched = body.percent_watched
            if body.percent_watched >= COMPLETION_THRESHOLD:
                progress.is_completed = True
            await db.commit()
            await db.refresh(progress)
    else:
        # Создаём новую запись
        is_completed = body.percent_watched >= COMPLETION_THRESHOLD
        progress = LessonProgress(
            user_id=user.id,
            lesson_id=body.lesson_id,
            percent_watched=body.percent_watched,
            is_completed=is_completed,
        )
        db.add(progress)
        await db.commit()
        await db.refresh(progress)

    return progress


# ---------------------------------------------------------------------------
# GET /api/v1/progress  — прогресс пользователя по всем урокам
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ProgressItem])
async def get_progress(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Возвращает массив прогресса текущего пользователя по всем урокам."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(LessonProgress).where(LessonProgress.user_id == user.id)
    )
    return result.scalars().all()
