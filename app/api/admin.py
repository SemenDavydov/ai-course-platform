import os
import shutil
from fastapi import File, UploadFile
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Cookie, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func
import secrets
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.models.material import Material
from app.models.user import User
from app.models.course import Course, Lesson, Module, Tariff, UserCourseAccess
from app.models.payment import Payment
from app.models.admin_session import AdminSession
from app.models.lesson_progress import LessonProgress
from app.models.site import WaitlistApplication
from app.services.access import grant_course_access, revoke_course_access, list_user_accesses
from app.services.site_settings import (
    get_landing_mode,
    set_landing_mode,
)
from app.config import settings
from app.templating import templates
from app.schemas.admin import *
from typing import Optional

router = APIRouter(prefix="/admin", tags=["admin"])

STALE_PENDING_DAYS = 2

# Секретный код для регистрации админов (храни в .env)
ADMIN_SECRET_CODE = settings.ADMIN_SECRET_CODE or "admin123"


def _payment_is_deletable(payment: Payment) -> bool:
    if payment.status != "pending" or not payment.created_at:
        return False
    created = payment.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_PENDING_DAYS)
    return created < cutoff


async def get_current_admin(
        request: Request,
        db: AsyncSession = Depends(get_db)
) -> User:
    """Проверяет, авторизован ли админ, и возвращает пользователя"""
    session_token = request.cookies.get("admin_session")

    if not session_token:
        raise HTTPException(status_code=303, detail="Redirecting to login")

    # Проверяем сессию в БД
    session_query = select(AdminSession).where(
        AdminSession.session_token == session_token,
        AdminSession.expires_at > datetime.utcnow()
    )
    session_result = await db.execute(session_query)
    session = session_result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=303, detail="Redirecting to login")

    # Получаем пользователя
    user = await db.get(User, session.user_id)
    if not user or user.role not in ["admin", "superadmin"]:
        raise HTTPException(status_code=303, detail="Redirecting to login")

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

    if not user or not user.check_password(password):
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Неверные учетные данные"}
        )

    if user.role not in ["admin", "superadmin"]:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Недостаточно прав"}
        )

    # Создаём сессию
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)

    session = AdminSession(
        user_id=user.id,
        session_token=session_token,
        expires_at=expires_at
    )
    db.add(session)
    await db.commit()

    # Устанавливаем cookie
    response.set_cookie(
        key="admin_session",
        value=session_token,
        max_age=86400,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax"
    )

    # Возвращаем RedirectResponse
    redirect_response = RedirectResponse(url="/admin/dashboard", status_code=303)

    # Копируем cookie из response в redirect_response
    for key, value in response.headers.items():
        if key.lower() == 'set-cookie':
            redirect_response.headers.append('set-cookie', value)

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
        admin: User = Depends(get_current_admin),
        course_id: Optional[str] = Query(None),
):
    courses = list(
        (
            await db.execute(
                select(Course).order_by(Course.sort_order.asc(), Course.id.asc())
            )
        ).scalars().all()
    )
    selected_course = None
    course_id_int: Optional[int] = None
    if course_id and str(course_id).isdigit():
        course_id_int = int(course_id)
        selected_course = next((c for c in courses if c.id == course_id_int), None)
        if selected_course is None:
            course_id_int = None
    course_id = course_id_int

    pay_q = select(Payment).where(Payment.status == "succeeded")
    if course_id is not None:
        pay_q = pay_q.where(Payment.course_id == course_id)

    users_count = await db.scalar(select(func.count()).select_from(User)) or 0
    blocked_users = await db.scalar(
        select(func.count()).select_from(User).where(User.is_blocked == True)
    ) or 0

    if course_id is not None:
        paid_users = await db.scalar(
            select(func.count(func.distinct(UserCourseAccess.user_id))).where(
                UserCourseAccess.course_id == course_id
            )
        ) or 0
        lessons_count = await db.scalar(
            select(func.count()).select_from(Lesson).where(Lesson.course_id == course_id)
        ) or 0
    else:
        paid_users = await db.scalar(
            select(func.count()).select_from(User).where(User.has_access == True)
        ) or 0
        lessons_count = await db.scalar(select(func.count()).select_from(Lesson)) or 0

    succeeded = list((await db.execute(pay_q)).scalars().all())

    def _provider_key(p: Payment) -> str:
        return (p.provider or "yookassa").strip().lower() or "yookassa"

    revenue_total = sum(float(p.amount or 0) for p in succeeded)
    today = datetime.now(timezone.utc).date()
    payments_today = 0
    for p in succeeded:
        dt = p.paid_at or p.created_at
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(timezone.utc).date() == today:
            payments_today += 1

    by_provider: dict[str, dict[str, float | int]] = {
        "yookassa": {"revenue": 0.0, "count": 0, "label": "Картой (ЮKassa)"},
        "proonline": {"revenue": 0.0, "count": 0, "label": "Рассрочка (ProOnline)"},
    }
    for p in succeeded:
        key = _provider_key(p)
        bucket = by_provider.setdefault(
            key, {"revenue": 0.0, "count": 0, "label": key}
        )
        bucket["revenue"] = float(bucket["revenue"]) + float(p.amount or 0)
        bucket["count"] = int(bucket["count"]) + 1

    provider_stats = [
        {
            "key": key,
            "label": data["label"],
            "revenue": float(data["revenue"]),
            "count": int(data["count"]),
        }
        for key, data in by_provider.items()
        if key in ("yookassa", "proonline") or float(data["revenue"]) > 0
    ]
    # stable order: yookassa, proonline, then others
    order = {"yookassa": 0, "proonline": 1}
    provider_stats.sort(key=lambda x: (order.get(x["key"], 99), x["key"]))

    # Per-course totals (always across all succeeded, for overview cards)
    course_revenue: dict[int | None, dict] = {}
    for c in courses:
        course_revenue[c.id] = {
            "course": c,
            "revenue": 0.0,
            "count": 0,
            "yookassa": 0.0,
            "proonline": 0.0,
        }
    course_revenue[None] = {
        "course": None,
        "revenue": 0.0,
        "count": 0,
        "yookassa": 0.0,
        "proonline": 0.0,
    }
    all_succeeded = list(
        (
            await db.execute(select(Payment).where(Payment.status == "succeeded"))
        ).scalars().all()
    )
    for p in all_succeeded:
        cid = p.course_id if p.course_id in course_revenue else None
        if p.course_id is not None and p.course_id not in course_revenue:
            cid = None
        bucket = course_revenue[cid]
        amt = float(p.amount or 0)
        bucket["revenue"] += amt
        bucket["count"] += 1
        pk = _provider_key(p)
        if pk == "proonline":
            bucket["proonline"] += amt
        else:
            bucket["yookassa"] += amt

    course_stats = []
    for cid, data in course_revenue.items():
        if cid is None and data["count"] == 0:
            continue
        c = data["course"]
        if c is None:
            title = "Без курса / старые платежи"
            badge = "archive"
        else:
            title = c.title
            badge = "legacy" if c.is_legacy else "story"
        course_stats.append(
            {
                "course_id": cid,
                "title": title,
                "badge": badge,
                "revenue": data["revenue"],
                "count": data["count"],
                "yookassa": data["yookassa"],
                "proonline": data["proonline"],
            }
        )

    month_buckets: dict[tuple[int, int], dict[str, float | int]] = {}
    for p in succeeded:
        dt = p.paid_at or p.created_at
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        key = (dt.year, dt.month)
        bucket = month_buckets.setdefault(
            key, {"revenue": 0.0, "payments_count": 0, "yookassa": 0.0, "proonline": 0.0}
        )
        amt = float(p.amount or 0)
        bucket["revenue"] = float(bucket["revenue"]) + amt
        bucket["payments_count"] = int(bucket["payments_count"]) + 1
        if _provider_key(p) == "proonline":
            bucket["proonline"] = float(bucket["proonline"]) + amt
        else:
            bucket["yookassa"] = float(bucket["yookassa"]) + amt

    month_names = (
        "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
        "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек",
    )
    monthly_revenue = []
    max_monthly_revenue = 0.0
    for (year, month), data in sorted(month_buckets.items(), reverse=True)[:12]:
        revenue = float(data["revenue"])
        max_monthly_revenue = max(max_monthly_revenue, revenue)
        monthly_revenue.append({
            "label": f"{month_names[month - 1]} {year}",
            "revenue": revenue,
            "payments_count": int(data["payments_count"]),
            "yookassa": float(data["yookassa"]),
            "proonline": float(data["proonline"]),
        })

    recent_q = (
        select(Payment)
        .where(Payment.status == "succeeded")
        .order_by(Payment.created_at.desc())
        .limit(8)
    )
    if course_id is not None:
        recent_q = recent_q.where(Payment.course_id == course_id)
    recent_payments = list((await db.execute(recent_q)).scalars().all())
    course_by_id = {c.id: c for c in courses}
    for p in recent_payments:
        p.user = await db.get(User, p.user_id)
        p.course_obj = course_by_id.get(p.course_id) if p.course_id else None

    recent_users = list(
        (
            await db.execute(select(User).order_by(User.created_at.desc()).limit(5))
        ).scalars().all()
    )

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "admin": admin,
            "courses": courses,
            "selected_course_id": course_id,
            "selected_course": selected_course,
            "stats": {
                "users": users_count,
                "paid_users": paid_users,
                "blocked_users": blocked_users,
                "revenue": int(revenue_total),
                "payments_today": payments_today,
                "courses": len(courses),
                "lessons": lessons_count,
            },
            "provider_stats": provider_stats,
            "course_stats": course_stats,
            "recent_payments": recent_payments,
            "recent_users": recent_users,
            "monthly_revenue": monthly_revenue,
            "max_monthly_revenue": max_monthly_revenue,
        },
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
    """Включает/отключает доступ к primary (AI STORY) курсу — legacy-совместимость."""
    from app.services.access import get_primary_course

    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    course = await get_primary_course(db)
    if not course:
        user.has_access = not user.has_access
        if user.has_access:
            user.access_granted_at = datetime.utcnow()
        await db.commit()
        return {"status": "ok", "has_access": user.has_access}

    existing = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user.id,
            UserCourseAccess.course_id == course.id,
        )
    )
    access = existing.scalar_one_or_none()
    if access:
        await revoke_course_access(db, user, course.id, commit=True)
        return {"status": "ok", "has_access": user.has_access}
    await grant_course_access(db, user, course.id, "pro", commit=True)
    return {"status": "ok", "has_access": user.has_access}


@router.post("/users/{user_id}/grant-course")
async def grant_user_course(
        user_id: int,
        course_id: int = Form(...),
        tariff_slug: str = Form("pro"),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    course = await db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    slug = tariff_slug if tariff_slug in ("legacy", "pro", "vip") else "pro"
    await grant_course_access(db, user, course.id, slug, commit=True)
    return RedirectResponse(url=f"/admin/users/{user_id}?granted=1#access", status_code=303)


@router.post("/users/{user_id}/revoke-course")
async def revoke_user_course(
        user_id: int,
        course_id: int = Form(...),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await revoke_course_access(db, user, course_id, commit=True)
    return RedirectResponse(url=f"/admin/users/{user_id}?revoked=1#access", status_code=303)


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


# Управление курсами
@router.get("/course", response_class=HTMLResponse)
async def admin_course(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
        course_id: Optional[int] = None,
):
    from sqlalchemy.orm import selectinload

    courses_result = await db.execute(
        select(Course).order_by(Course.sort_order.asc(), Course.id.asc())
    )
    courses = list(courses_result.scalars().all())

    course = None
    if course_id:
        course = next((c for c in courses if c.id == course_id), None)
    if course is None and courses:
        # Prefer AI STORY
        course = next((c for c in courses if not c.is_legacy), courses[0])

    modules = []
    lessons = []
    tariffs = []
    if course:
        modules_result = await db.execute(
            select(Module)
            .where(Module.course_id == course.id)
            .order_by(Module.order)
        )
        modules = list(modules_result.scalars().all())

        lessons_result = await db.execute(
            select(Lesson)
            .where(Lesson.course_id == course.id)
            .options(selectinload(Lesson.materials), selectinload(Lesson.module))
            .order_by(Lesson.order)
        )
        lessons = list(lessons_result.scalars().all())

        tariffs_result = await db.execute(
            select(Tariff)
            .where(Tariff.course_id == course.id)
            .order_by(Tariff.sort_order, Tariff.price)
        )
        tariffs = list(tariffs_result.scalars().all())

    return templates.TemplateResponse(
        "admin/course.html",
        {
            "request": request,
            "admin": admin,
            "courses": courses,
            "course": course,
            "modules": modules,
            "lessons": lessons,
            "tariffs": tariffs,
        },
    )


@router.post("/course/update")
async def update_course(
        request: Request,
        title: str = Form(...),
        description: str = Form(...),
        price: float = Form(...),
        is_published: bool = Form(False),
        course_id: Optional[int] = Form(None),
        slug: Optional[str] = Form(None),
        sort_order: int = Form(0),
        is_legacy: bool = Form(False),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    course = None
    if course_id:
        course = await db.get(Course, course_id)

    if course:
        course.title = title
        course.description = description
        course.price = price
        course.is_published = is_published
        course.sort_order = sort_order
        course.is_legacy = is_legacy
        if slug:
            course.slug = slug.strip()
    else:
        course = Course(
            title=title,
            description=description,
            price=price,
            is_published=is_published,
            slug=(slug or None),
            sort_order=sort_order,
            is_legacy=is_legacy,
        )
        db.add(course)

    await db.commit()
    await db.refresh(course)
    return RedirectResponse(url=f"/admin/course?course_id={course.id}", status_code=303)


@router.post("/modules/add")
async def add_module(
        course_id: int = Form(...),
        title: str = Form(...),
        order: int = Form(...),
        button_label: str = Form(""),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    course = await db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    module = Module(
        course_id=course_id,
        title=title,
        order=order,
        button_label=button_label.strip() or f"Модуль {order}",
    )
    db.add(module)
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={course_id}", status_code=303)


@router.post("/modules/{module_id}/update")
async def update_module(
        module_id: int,
        title: str = Form(...),
        order: int = Form(...),
        button_label: str = Form(""),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    module = await db.get(Module, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    module.title = title
    module.order = order
    module.button_label = button_label.strip() or f"Модуль {order}"
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={module.course_id}", status_code=303)


@router.post("/modules/{module_id}/delete")
async def delete_module(
        module_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    module = await db.get(Module, module_id)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    course_id = module.course_id
    # Detach lessons before delete (module cascade may delete lessons — SET NULL on FK)
    lessons_result = await db.execute(select(Lesson).where(Lesson.module_id == module_id))
    for lesson in lessons_result.scalars().all():
        lesson.module_id = None
    await db.delete(module)
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={course_id}", status_code=303)


@router.post("/tariffs/{tariff_id}/update")
async def update_tariff(
        tariff_id: int,
        name: str = Form(...),
        price: float = Form(...),
        old_price: Optional[float] = Form(None),
        is_active: bool = Form(False),
        features_markdown: str = Form(""),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    tariff = await db.get(Tariff, tariff_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    tariff.name = name
    tariff.price = price
    tariff.old_price = old_price
    tariff.is_active = is_active
    tariff.features_markdown = features_markdown
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={tariff.course_id}", status_code=303)


@router.post("/lessons/add")
async def add_lesson(
        request: Request,
        title: str = Form(...),
        description: str = Form(...),
        video_id: str = Form(""),
        order: int = Form(...),
        course_id: int = Form(...),
        module_id: Optional[int] = Form(None),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    course = await db.get(Course, course_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    mid = module_id if module_id and module_id > 0 else None
    lesson = Lesson(
        course_id=course.id,
        module_id=mid,
        title=title,
        description=description,
        video_id=video_id.strip() if video_id.strip() else "pending",
        order=order,
    )
    db.add(lesson)
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={course.id}", status_code=303)


import uuid as _uuid


@router.post("/lessons/{lesson_id}/upload-material")
async def upload_material(
        lesson_id: int,
        title: str = Form(...),
        file: UploadFile = File(...),
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail=f"Lesson with id {lesson_id} not found")

    upload_dir = "uploads/materials"
    os.makedirs(upload_dir, exist_ok=True)

    file_extension = os.path.splitext(file.filename)[1]
    file_name = f"{_uuid.uuid4()}{file_extension}"
    file_path = os.path.join(upload_dir, file_name)

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    material = Material(
        lesson_id=lesson_id,
        title=title,
        file_name=file_name,
        original_name=file.filename,
        file_size=len(content),
        file_type=file.content_type or "application/octet-stream",
    )
    db.add(material)
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={lesson.course_id}", status_code=303)


@router.post("/lessons/{lesson_id}/update")
async def update_lesson(
    lesson_id: int,
    title: str = Form(...),
    description: str = Form(...),
    video_id: str = Form(""),
    order: int = Form(...),
    module_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    lesson.title = title
    lesson.description = description
    lesson.video_id = video_id.strip() if video_id.strip() else "pending"
    lesson.order = order
    if module_id is not None:
        lesson.module_id = module_id if module_id > 0 else None

    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={lesson.course_id}", status_code=303)


@router.post("/lessons/{lesson_id}/delete")
async def delete_lesson(
        lesson_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    course_id = lesson.course_id
    await db.delete(lesson)
    await db.commit()
    return RedirectResponse(url=f"/admin/course?course_id={course_id}", status_code=303)


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(
        user_id: int,
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    progress_result = await db.execute(
        select(LessonProgress, Lesson)
        .join(Lesson, LessonProgress.lesson_id == Lesson.id)
        .where(LessonProgress.user_id == user_id)
        .order_by(Lesson.order)
    )
    progress_rows = progress_result.all()

    payments_result = await db.execute(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )
    payments = payments_result.scalars().all()

    accesses = await list_user_accesses(db, user_id)
    all_courses = (
        await db.execute(select(Course).order_by(Course.sort_order, Course.id))
    ).scalars().all()
    granted_ids = {a.course_id for a in accesses}
    available_courses = [c for c in all_courses if c.id not in granted_ids]

    return templates.TemplateResponse(
        "admin/user_detail.html",
        {
            "request": request,
            "admin": admin,
            "user": user,
            "progress_rows": progress_rows,
            "payments": payments,
            "accesses": accesses,
            "all_courses": all_courses,
            "available_courses": available_courses,
            "granted": request.query_params.get("granted") == "1",
            "revoked": request.query_params.get("revoked") == "1",
        },
    )


@router.get("/payments", response_class=HTMLResponse)
async def admin_payments(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
        page: int = 1,
        course_id: Optional[str] = Query(None),
        provider: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
):
    per_page = 20
    offset = (page - 1) * per_page

    courses = list(
        (
            await db.execute(
                select(Course).order_by(Course.sort_order.asc(), Course.id.asc())
            )
        ).scalars().all()
    )
    course_by_id = {c.id: c for c in courses}

    provider_filter = (provider or "").strip().lower() or None
    if provider_filter and provider_filter not in ("yookassa", "proonline"):
        provider_filter = None
    status_filter = (status or "").strip().lower() or None
    if status_filter and status_filter not in ("succeeded", "pending", "canceled", "failed"):
        status_filter = None

    selected_course_id: Optional[int] = None
    filters = []
    if course_id is not None and str(course_id).strip() != "":
        if str(course_id).strip() == "0":
            filters.append(Payment.course_id.is_(None))
            selected_course_id = 0  # sentinel: unassigned
        elif str(course_id).isdigit() and int(course_id) in course_by_id:
            selected_course_id = int(course_id)
            filters.append(Payment.course_id == selected_course_id)
    if provider_filter:
        filters.append(Payment.provider == provider_filter)
    if status_filter:
        filters.append(Payment.status == status_filter)

    base_q = select(Payment)
    count_q = select(func.count()).select_from(Payment)
    if filters:
        base_q = base_q.where(*filters)
        count_q = count_q.where(*filters)

    payments = list(
        (
            await db.execute(
                base_q.order_by(Payment.created_at.desc()).offset(offset).limit(per_page)
            )
        ).scalars().all()
    )

    for payment in payments:
        payment.user = await db.get(User, payment.user_id)
        payment.course_obj = course_by_id.get(payment.course_id) if payment.course_id else None

    total = await db.scalar(count_q) or 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_PENDING_DAYS)
    stale_count = await db.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.status == "pending",
            Payment.created_at < cutoff,
        )
    ) or 0

    deleted = request.query_params.get("deleted")

    def _filter_qs(page_num: int) -> str:
        parts = [f"page={page_num}"]
        if selected_course_id is not None:
            parts.append(f"course_id={selected_course_id}")
        if provider_filter:
            parts.append(f"provider={provider_filter}")
        if status_filter:
            parts.append(f"status={status_filter}")
        return "&".join(parts)

    return templates.TemplateResponse(
        "admin/payments.html",
        {
            "request": request,
            "admin": admin,
            "payments": payments,
            "courses": courses,
            "page": page,
            "total": total,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "stale_count": stale_count,
            "deleted": deleted,
            "payment_is_deletable": _payment_is_deletable,
            "selected_course_id": selected_course_id,
            "selected_provider": provider_filter,
            "selected_status": status_filter,
            "filter_qs": _filter_qs,
        },
    )


@router.post("/payments/delete-stale")
async def delete_stale_payments(
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_PENDING_DAYS)
    result = await db.execute(
        delete(Payment).where(
            Payment.status == "pending",
            Payment.created_at < cutoff,
        )
    )
    await db.commit()
    deleted = result.rowcount or 0
    return RedirectResponse(url=f"/admin/payments?deleted={deleted}", status_code=303)


@router.post("/payments/{payment_id}/delete")
async def delete_payment(
        payment_id: int,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if not _payment_is_deletable(payment):
        raise HTTPException(
            status_code=400,
            detail="Можно удалить только платежи в статусе «Ожидание» старше 2 дней",
        )
    await db.delete(payment)
    await db.commit()
    return RedirectResponse(url="/admin/payments?deleted=1", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
):
    mode = await get_landing_mode(db)
    return templates.TemplateResponse(
        "admin/settings.html",
        {
            "request": request,
            "admin": admin,
            "landing_mode": mode,
            "saved": request.query_params.get("saved"),
        },
    )


@router.post("/settings/landing-mode")
async def admin_set_landing_mode(
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
        mode: str = Form(...),
):
    try:
        await set_landing_mode(db, mode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Некорректный режим")
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)


@router.get("/waitlist", response_class=HTMLResponse)
async def admin_waitlist(
        request: Request,
        db: AsyncSession = Depends(get_db),
        admin: User = Depends(get_current_admin),
        page: int = 1,
):
    per_page = 30
    offset = max(0, (page - 1) * per_page)
    total = await db.scalar(select(func.count()).select_from(WaitlistApplication)) or 0
    rows = list(
        (
            await db.execute(
                select(WaitlistApplication)
                .order_by(WaitlistApplication.created_at.desc())
                .offset(offset)
                .limit(per_page)
            )
        ).scalars().all()
    )
    return templates.TemplateResponse(
        "admin/waitlist.html",
        {
            "request": request,
            "admin": admin,
            "applications": rows,
            "page": page,
            "total": total,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        },
    )