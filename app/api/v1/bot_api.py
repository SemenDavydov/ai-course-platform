import jwt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Request
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from app.config import settings
from app.database import get_db
from app.models.material import Material
from app.models.user import User
from app.models.course import Course, Lesson
from app.services.video import VideoService

router = APIRouter(prefix="/api/v1/bot", tags=["bot-api"])

templates = Jinja2Templates(directory="app/templates")

# Pydantic модели для ответов
class UserResponse(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    has_access: bool
    email: Optional[str]

    class Config:
        from_attributes = True


class LessonResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    order: int
    video_id: str

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


@router.get("/user/{telegram_id}", response_model=UserResponse)
async def get_user(telegram_id: int, db: AsyncSession = Depends(get_db)):
    """
    Получает информацию о пользователе по telegram_id
    Используется ботом для проверки статуса
    """
    query = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


@router.get("/course", response_model=CourseResponse)
async def get_course(db: AsyncSession = Depends(get_db)):
    """
    Получает информацию о курсе со всеми уроками
    Используется ботом для отображения содержания
    """
    query = select(Course).where(Course.is_published == True)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    return course


@router.get("/lesson/{lesson_id}/video")
async def get_lesson_video(
        lesson_id: int,
        telegram_id: int,
        db: AsyncSession = Depends(get_db)
):
    """
    Генерирует временную ссылку на видео с водяным знаком
    Используется ботом, когда пользователь выбирает урок
    """
    # Проверяем существование урока
    lesson_query = select(Lesson).where(Lesson.id == lesson_id)
    lesson_result = await db.execute(lesson_query)
    lesson = lesson_result.scalar_one_or_none()

    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # Проверяем пользователя и его доступ
    user_query = select(User).where(User.telegram_id == telegram_id)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.has_access:
        raise HTTPException(status_code=403, detail="Access denied")

    # Генерируем ссылку на видео
    video_service = VideoService()
    video_link = await video_service.generate_watermarked_link(user, lesson.video_id)

    if not video_link:
        # Fallback на JWT-метод
        video_link = await video_service.generate_jwt_link(user, lesson.video_id)
        # В продакшене здесь должен быть полный URL
        video_link = f"https://your-domain.com{video_link}"

    return {
        "lesson_id": lesson.id,
        "lesson_title": lesson.title,
        "video_url": video_link,
        "expires_in": 7200,  # 2 часа в секундах
        "watermark": f"{user.email or user.telegram_id}"
    }


@router.post("/user/{telegram_id}/email")
async def update_user_email(
        telegram_id: int,
        email: str,
        db: AsyncSession = Depends(get_db)
):
    """
    Обновляет email пользователя (для чеков)
    """
    query = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.email = email
    await db.commit()

    return {"status": "ok", "message": "Email updated"}


@router.get("/health")
async def health_check():
    """Проверка работоспособности API"""
    return {
        "status": "healthy",
        "service": "bot-api",
        "timestamp": "2024-01-01T00:00:00Z"  # В реальном коде используй datetime.now()
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

        # Проверяем, что пользователь существует и имеет доступ
        user = await db.get(User, user_id)
        if not user or not user.has_access:
            return templates.TemplateResponse(
                "video/player.html",
                {
                    "request": request,
                    "error": "Доступ запрещён",
                    "lesson_title": "Ошибка доступа",
                    "bot_url": f"https://t.me/{settings.BOT_USERNAME}",
                }
            )

        # Получаем информацию об уроке
        lesson_query = select(Lesson).where(Lesson.video_id == video_id).limit(1)
        lesson_result = await db.execute(lesson_query)
        lesson = lesson_result.scalar_one_or_none()

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