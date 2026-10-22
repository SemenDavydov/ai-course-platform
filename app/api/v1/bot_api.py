import jwt
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional
from pydantic import BaseModel
from starlette.responses import HTMLResponse

from app.config import settings
from app.templating import templates
from app.database import get_db
from app.models.material import Material
from app.models.user import User
from app.models.course import Course, Lesson
from app.services.video import VideoService
from app.services.access import get_primary_course, user_has_course

router = APIRouter(prefix="/api/v1/bot", tags=["bot-api"])

# Эндпоинты /user/{telegram_id}, /user/{telegram_id}/email и
# /lesson/{id}/video?telegram_id= удалены: они работали без авторизации
# (утечка email, смена чужого email -> угон аккаунта, бесплатные видео).
# Бот ходит в БД напрямую и их не использовал.


class LessonResponse(BaseModel):
    # video_id намеренно не отдаём: это ID видео в Kinescope.
    id: int
    title: str
    description: Optional[str]
    order: int

    class Config:
        from_attributes = True


class CourseResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    price: float
    lessons: list[LessonResponse] = []

    class Config:
        from_attributes = True


@router.get("/course", response_model=CourseResponse)
async def get_course(db: AsyncSession = Depends(get_db), slug: Optional[str] = None):
    if slug:
        query = (
            select(Course)
            .where(Course.slug == slug, Course.is_published == True)
            .options(selectinload(Course.lessons))
        )
        result = await db.execute(query)
        course = result.scalar_one_or_none()
    else:
        course = await get_primary_course(db)
        if course:
            result = await db.execute(
                select(Course)
                .where(Course.id == course.id)
                .options(selectinload(Course.lessons))
            )
            course = result.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    return course


@router.get("/health")
async def health_check():
    """Проверка работоспособности API"""
    return {
        "status": "healthy",
        "service": "bot-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/video/{video_id}", response_class=HTMLResponse)
async def watch_video(
        request: Request,
        video_id: str,
        token: str,
        db: AsyncSession = Depends(get_db)
):
    """
    Страница просмотра видео с плеером Kinescope
    """
    try:
        # Проверяем JWT токен
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"]
        )

        user_id = payload.get("user_id")
        video_id_from_token = payload.get("video_id")
        user_email = payload.get("email", "Unknown")

        # Проверяем, что видео ID совпадает
        if video_id_from_token != video_id:
            return templates.TemplateResponse(
                "video/player.html",
                {
                    "request": request,
                    "error": "Недействительная ссылка",
                    "lesson_title": "Ошибка доступа",
                    "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
                }
            )

        user = await db.get(User, user_id) if user_id else None

        lesson_query = select(Lesson).where(Lesson.video_id == video_id).limit(1)
        lesson_result = await db.execute(lesson_query)
        lesson = lesson_result.scalar_one_or_none()

        # Доступ проверяем к курсу конкретного урока, а не по глобальному
        # user.has_access: иначе покупатель одного курса видит видео другого.
        if (
            not user
            or not lesson
            or not await user_has_course(db, user, lesson.course_id)
        ):
            return templates.TemplateResponse(
                "video/player.html",
                {
                    "request": request,
                    "error": "Доступ запрещён",
                    "lesson_title": "Ошибка доступа",
                    "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
                }
            )

        if lesson:
            # Загружаем материалы для этого урока
            materials_query = select(Material).where(Material.lesson_id == lesson.id)
            materials_result = await db.execute(materials_query)
            materials = materials_result.scalars().all()
            print(f"📦 Found {len(materials)} materials for lesson {lesson.id}")
        else:
            materials = []

        # Получаем embed-ссылку из Kinescope API
        video_service = VideoService()
        embed_url = await video_service.get_embed_link(video_id)

        if not embed_url:
            return templates.TemplateResponse(
                "video/player.html",
                {
                    "request": request,
                    "error": "Видео временно недоступно",
                    "lesson_title": lesson.title if lesson else "Видео",
                    "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
                }
            )
        import urllib.parse
        parsed = urllib.parse.urlparse(embed_url)
        query_params = urllib.parse.parse_qs(parsed.query)

        # Используем email или telegram_id
        watermark_value = user.email or str(user.telegram_id)
        query_params['watermark'] = [watermark_value]

        # Собираем URL обратно
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        new_parts = list(parsed)
        new_parts[4] = new_query
        final_embed_url = urllib.parse.urlunparse(new_parts)

        return templates.TemplateResponse(
            "video/player.html",
            {
                "request": request,
                "embed_url": final_embed_url,
                "lesson_title": lesson.title if lesson else "Видеоурок",
                "lesson_description": lesson.description if lesson else "",
                "user_email": user.email or f"User {user.telegram_id}",
                "materials": materials,
                "token": token,
                "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
                "error": None
            }
        )

    except jwt.ExpiredSignatureError:
        return templates.TemplateResponse(
            "video/player.html",
            {
                "request": request,
                "error": "Срок действия ссылки истёк (2 часа). Вернитесь в бот за новой ссылкой.",
                "lesson_title": "Ссылка устарела",
                "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
            }
        )
    except jwt.InvalidTokenError:
        return templates.TemplateResponse(
            "video/player.html",
            {
                "request": request,
                "error": "Недействительная ссылка",
                "lesson_title": "Ошибка доступа",
                "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
            }
        )
    except Exception as e:
        return templates.TemplateResponse(
            "video/player.html",
            {
                "request": request,
                "error": "Произошла внутренняя ошибка",
                "lesson_title": "Ошибка",
                "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
            }
        )