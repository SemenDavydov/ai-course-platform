import os
import shutil
from fastapi import File, UploadFile
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Cookie
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func
import secrets
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.models.material import Material
from app.models.user import User
from app.models.course import Course, Lesson
from app.models.payment import Payment
from app.models.admin_session import AdminSession
from app.config import settings
from app.schemas.admin import *

router = APIRouter(prefix="/admin", tags=["admin"])

# Настройка шаблонов
templates = Jinja2Templates(directory="app/templates")

# Секретный код для регистрации админов (храни в .env)
ADMIN_SECRET_CODE = settings.ADMIN_SECRET_CODE or "admin123"


async def get_current_admin(
        request: Request,
        db: AsyncSession = Depends(get_db)
) -> User:
    """Проверяет, авторизован ли админ, и возвращает пользователя"""
    # Добавим отладку
    print("Cookies received:", request.cookies)

    session_token = request.cookies.get("admin_session")
    print("Session token:", session_token)

    if not session_token:
        print("No session token in cookies")
        raise HTTPException(status_code=303, detail="Redirecting to login")

    # Проверяем сессию в БД
    session_query = select(AdminSession).where(
        AdminSession.session_token == session_token,
        AdminSession.expires_at > datetime.utcnow()
    )
    session_result = await db.execute(session_query)
    session = session_result.scalar_one_or_none()

    if not session:
        print(f"Session not found or expired: {session_token}")
        raise HTTPException(status_code=303, detail="Redirecting to login")

    # Получаем пользователя
    user = await db.get(User, session.user_id)
    if not user or user.role not in ["admin", "superadmin"]:
        print(f"User not found or not admin: {session.user_id}")
        raise HTTPException(status_code=303, detail="Redirecting to login")

    print(f"Admin authenticated: {user.username}")
    return user


# Страница регистрации
@router.get("/register", response_class=HTMLResponse)
async def admin_register_page(request: Request):
    return templates.TemplateResponse(
        "admin/register.html",
        {"request": request}
    )


@router.post("/register")
async def admin_register(
        request: Request,
        username: str = Form(...),
        email: str = Form(...),
        password: str = Form(...),
        admin_code: str = Form(...),
        db: AsyncSession = Depends(get_db)
):
    # Проверяем секретный код
    if admin_code != ADMIN_SECRET_CODE:
        return templates.TemplateResponse(
            "admin/register.html",
            {"request": request, "error": "Неверный код регистрации"}
        )

    # Проверяем, не занят ли username
    user_query = select(User).where(User.username == username)
    user_result = await db.execute(user_query)
    if user_result.scalar_one_or_none():
        return templates.TemplateResponse(
            "admin/register.html",
            {"request": request, "error": "Имя пользователя уже занято"}
        )

    # Создаём нового админа
    new_admin = User(
        username=username,
        email=email,
        role="admin",
        is_admin=True
    )
    new_admin.set_password(password)

    db.add(new_admin)
    await db.commit()

    return RedirectResponse(url="/admin/login", status_code=303)


# Обновлённый логин
@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request}
    )


@router.post("/login")
async def admin_login_post(
        request: Request,
        response: Response,
        username: str = Form(...),
        password: str = Form(...),
        db: AsyncSession = Depends(get_db)
):
    # Ищем пользователя
    user_query = select(User).where(User.username == username)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()

    print(f"Login attempt for user: {username}")
    print(f"User found: {user is not None}")

    if not user or not user.check_password(password):
        print("Invalid credentials")
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Неверные учетные данные"}
        )

    if user.role not in ["admin", "superadmin"]:
        print(f"Insufficient role: {user.role}")
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Недостаточно прав"}
        )

    # Создаём сессию
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)

    print(f"Creating session with token: {session_token}")

    session = AdminSession(
        user_id=user.id,
        session_token=session_token,
        expires_at=expires_at
    )
    db.add(session)
    await db.commit()

    # Устанавливаем cookie с явными параметрами
    response.set_cookie(
        key="admin_session",
        value=session_token,
        max_age=86400,
        path="/",
        httponly=False,  # Временно ставим False для отладки
        secure=False,
        samesite="lax"
    )

    print(f"Cookie set. Headers after set_cookie: {response.headers}")

    # Возвращаем RedirectResponse
    redirect_response = RedirectResponse(url="/admin/dashboard", status_code=303)

    # Копируем cookie из response в redirect_response
    for key, value in response.headers.items():
        if key.lower() == 'set-cookie':
            redirect_response.headers.append('set-cookie', value)
            print(f"Cookie forwarded: {value}")

    return redirect_response


@router.get("/logout")
async def admin_logout(
        request: Request,
        response: Response,
        db: AsyncSession = Depends(get_db)
):
    session_token = request.cookies.get("admin_session")
    if session_token:
        await db.execute(
            delete(AdminSession).where(AdminSession.session_token == session_token)
        )
        await db.commit()

    response.delete_cookie("admin_session")
    return RedirectResponse(url="/admin/login")


# Главная панель
@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    # Собираем статистику
    users_count = await db.scalar(select(func.count()).select_from(User))
    paid_users = await db.scalar(
        select(func.count()).select_from(User).where(User.has_access == True)
    )
    blocked_users = await db.scalar(
        select(func.count()).select_from(User).where(User.is_blocked == True)
    )

    payments_sum = await db.scalar(
        select(func.sum(Payment.amount)).where(Payment.status == "succeeded")
    )

    payments_today = await db.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.status == "succeeded",
            Payment.paid_at >= datetime.utcnow().date()
        )
    )

    courses_count = await db.scalar(select(func.count()).select_from(Course))
    lessons_count = await db.scalar(select(func.count()).select_from(Lesson))

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "admin": admin,
            "stats": {
                "users": users_count or 0,
                "paid_users": paid_users or 0,
                "blocked_users": blocked_users or 0,
                "revenue": payments_sum or 0,
                "payments_today": payments_today or 0,
                "courses": courses_count or 0,
                "lessons": lessons_count or 0
            }
        }
    )


# Управление пользователями
@router.get("/users", response_class=HTMLResponse)
async def admin_users(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
        page: int = 1,
        search: Optional[str] = None
):
    per_page = 20
    offset = (page - 1) * per_page

    query = select(User)
    if search:
        query = query.where(
            (User.username.contains(search)) |
            (User.telegram_id.contains(search)) |
            (User.email.contains(search))
        )

    users = await db.execute(
        query.order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    users = users.scalars().all()

    total = await db.scalar(select(func.count()).select_from(User))

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "admin": admin,
            "users": users,
            "page": page,
            "total_pages": (total // per_page) + 1,
            "search": search
        }
    )


@router.post("/users/{user_id}/toggle-block")
async def toggle_user_block(
        user_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    """Блокирует/разблокирует пользователя"""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_blocked = not user.is_blocked
    await db.commit()

    return {"status": "ok", "is_blocked": user.is_blocked}


@router.post("/users/{user_id}/toggle-access")
async def toggle_user_access(
        user_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    """Включает/отключает доступ пользователя к курсу"""
    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.has_access = not user.has_access
    if user.has_access:
        user.access_granted_at = datetime.utcnow()

    await db.commit()
    return {"status": "ok", "has_access": user.has_access}


@router.post("/users/{user_id}/set-role")
async def set_user_role(
        user_id: int,
        role: str,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    """Изменяет роль пользователя (только для superadmin)"""
    if admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only superadmin can change roles")

    if role not in ["user", "admin", "superadmin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = role
    user.is_admin = (role in ["admin", "superadmin"])
    await db.commit()

    return {"status": "ok", "role": user.role}


# Управление курсом
@router.get("/course", response_class=HTMLResponse)
async def admin_course(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    # Загружаем курс
    course_result = await db.execute(select(Course))
    course = course_result.scalar_one_or_none()

    # Загружаем уроки с материалами
    from sqlalchemy.orm import selectinload
    lessons_result = await db.execute(
        select(Lesson).options(selectinload(Lesson.materials)).order_by(Lesson.order)
    )
    lessons = lessons_result.scalars().all()

    # Отладочная информация
    print("\n=== УРОКИ И МАТЕРИАЛЫ ===")
    for lesson in lessons:
        print(f"Урок {lesson.id}: {lesson.title}")
        if lesson.materials:
            for m in lesson.materials:
                print(f"  📎 {m.id}: {m.title}")
        else:
            print("  ❌ Нет материалов")

    return templates.TemplateResponse(
        "admin/course.html",
        {
            "request": request,
            "admin": admin,
            "course": course,
            "lessons": lessons
        }
    )


@router.post("/course/update")
async def update_course(
        request: Request,
        title: str = Form(...),
        description: str = Form(...),
        price: float = Form(...),
        is_published: bool = Form(False),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    course = await db.execute(select(Course))
    course = course.scalar_one_or_none()

    if course:
        course.title = title
        course.description = description
        course.price = price
        course.is_published = is_published
    else:
        course = Course(
            title=title,
            description=description,
            price=price,
            is_published=is_published
        )
        db.add(course)

    await db.commit()
    return RedirectResponse(url="/admin/course", status_code=303)


# Управление уроками
@router.post("/lessons/add")
async def add_lesson(
        request: Request,
        title: str = Form(...),
        description: str = Form(...),
        video_id: str = Form(""),
        order: int = Form(...),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    # Получаем курс
    course = await db.execute(select(Course))
    course = course.scalar_one_or_none()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    lesson = Lesson(
        course_id=course.id,
        title=title,
        description=description,
        video_id=video_id if video_id.strip() else "",  # Пустая строка вместо NULL
        order=order
    )
    db.add(lesson)
    await db.commit()

    return RedirectResponse(url="/admin/course", status_code=303)


import os
import uuid
from fastapi import File, UploadFile, Form
from app.models.material import Material


@router.post("/lessons/{lesson_id}/upload-material")
async def upload_material(
        lesson_id: int,
        title: str = Form(...),
        file: UploadFile = File(...),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    """Загрузка материала к уроку"""

    # Проверяем существование урока
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail=f"Lesson with id {lesson_id} not found")

    # Создаём папку для загрузок
    upload_dir = "uploads/materials"
    os.makedirs(upload_dir, exist_ok=True)

    # Генерируем уникальное имя файла
    import uuid
    file_extension = os.path.splitext(file.filename)[1]
    file_name = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(upload_dir, file_name)

    # Сохраняем файл
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    material = Material(
        lesson_id=lesson_id,
        title=title,
        file_name=file_name,
        original_name=file.filename,
        file_size=len(content),
        file_type=file.content_type or "application/octet-stream"
    )

    db.add(material)
    await db.commit()

    print(f"Материал сохранён: {title} для урока {lesson_id}")

    return RedirectResponse(url="/admin/course", status_code=303)


@router.post("/lessons/{lesson_id}/update")
async def update_lesson(
    lesson_id: int,
    title: str = Form(...),
    description: str = Form(...),
    video_id: str = Form(""),
    order: int = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Обновление существующего урока"""
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    lesson.title = title
    lesson.description = description
    lesson.video_id = video_id if video_id.strip() else ""
    lesson.order = order

    await db.commit()
    return RedirectResponse(url="/admin/course", status_code=303)


@router.post("/lessons/{lesson_id}/delete")
async def delete_lesson(
        lesson_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin)
):
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    await db.delete(lesson)
    await db.commit()

    return RedirectResponse(url="/admin/course", status_code=303)


# Платежи
@router.get("/payments", response_class=HTMLResponse)
async def admin_payments(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
        page: int = 1
):
    per_page = 20
    offset = (page - 1) * per_page

    payments = await db.execute(
        select(Payment)
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    payments = payments.scalars().all()

    # Загружаем пользователей
    for payment in payments:
        user = await db.get(User, payment.user_id)
        payment.user = user

    total = await db.scalar(select(func.count()).select_from(Payment))

    return templates.TemplateResponse(
        "admin/payments.html",
        {
            "request": request,
            "admin": admin,
            "payments": payments,
            "page": page,
            "total_pages": (total // per_page) + 1
        }
    )