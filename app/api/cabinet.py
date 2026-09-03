"""
Cabinet router: личный кабинет пользователя.
Prefix: /cabinet
"""
import os
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.course import Course, Lesson, Module, Tariff
from app.models.lesson_progress import LessonProgress
from app.models.material import Material
from app.services.auth import get_current_user
from app.services.access import (
    list_accessible_courses,
    list_user_accesses,
    user_has_course,
    get_primary_course,
)
from app.services.video import VideoService
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cabinet", tags=["cabinet"])

AVATAR_DIR = "app/static/uploads/avatars"
AVATAR_MAX_BYTES = 5 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


async def _load_primary_tariffs(db: AsyncSession) -> tuple[Course | None, list[Tariff]]:
    course = await get_primary_course(db)
    if not course:
        return None, []
    t_result = await db.execute(
        select(Tariff)
        .where(Tariff.course_id == course.id, Tariff.is_active == True)
        .order_by(Tariff.sort_order, Tariff.price)
    )
    return course, list(t_result.scalars().all())


async def _user_needs_paywall(db: AsyncSession, user) -> bool:
    if not user.has_access:
        return True
    accessible = await list_accessible_courses(db, user.id)
    return not accessible


def _render_lessons_paywall(request: Request, user, course: Course | None, tariffs: list[Tariff]):
    return templates.TemplateResponse("cabinet/lessons_paywall.html", {
        "request": request,
        "user": user,
        "course": course,
        "tariffs": tariffs,
    })


@router.get("", response_class=HTMLResponse)
async def cabinet_index(request: Request):
    return RedirectResponse("/cabinet/lessons", status_code=302)


@router.get("/lessons", response_class=HTMLResponse)
async def lessons_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    course_id: Optional[int] = None,
):
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=302)

    if await _user_needs_paywall(db, user):
        course, tariffs = await _load_primary_tariffs(db)
        return _render_lessons_paywall(request, user, course, tariffs)

    accessible = await list_accessible_courses(db, user.id)

    course = None
    if course_id:
        course = next((c for c in accessible if c.id == course_id), None)
    if course is None:
        course = accessible[0]

    progress_result = await db.execute(
        select(LessonProgress).where(LessonProgress.user_id == user.id)
    )
    progress_map = {p.lesson_id: p for p in progress_result.scalars().all()}

    modules_result = await db.execute(
        select(Module)
        .where(Module.course_id == course.id)
        .options(selectinload(Module.lessons).selectinload(Lesson.materials))
        .order_by(Module.order)
    )
    modules = list(modules_result.scalars().unique().all())

    modules_with_progress = []
    for module in modules:
        lessons_wp = []
        for lesson in sorted(module.lessons, key=lambda L: L.order):
            p = progress_map.get(lesson.id)
            lessons_wp.append({
                "lesson": lesson,
                "percent_watched": p.percent_watched if p else 0,
                "is_completed": p.is_completed if p else False,
            })
        modules_with_progress.append({"module": module, "lessons": lessons_wp})

    # Flat lessons without module (legacy)
    orphan_result = await db.execute(
        select(Lesson)
        .where(Lesson.course_id == course.id, Lesson.module_id.is_(None))
        .order_by(Lesson.order)
    )
    orphan_lessons = []
    for lesson in orphan_result.scalars().all():
        p = progress_map.get(lesson.id)
        orphan_lessons.append({
            "lesson": lesson,
            "percent_watched": p.percent_watched if p else 0,
            "is_completed": p.is_completed if p else False,
        })

    accesses = await list_user_accesses(db, user.id)
    access_map = {a.course_id: a for a in accesses}

    return templates.TemplateResponse("cabinet/lessons.html", {
        "request": request,
        "user": user,
        "course": course,
        "accessible_courses": accessible,
        "modules_with_progress": modules_with_progress,
        "orphan_lessons": orphan_lessons,
        "access_map": access_map,
    })


@router.get("/lessons/{lesson_id}", response_class=HTMLResponse)
async def lesson_player(
    lesson_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=302)

    if await _user_needs_paywall(db, user):
        course, tariffs = await _load_primary_tariffs(db)
        return _render_lessons_paywall(request, user, course, tariffs)

    lesson_result = await db.execute(
        select(Lesson)
        .where(Lesson.id == lesson_id)
        .options(selectinload(Lesson.module))
    )
    lesson = lesson_result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Урок не найден")

    if not await user_has_course(db, user, lesson.course_id):
        course, tariffs = await _load_primary_tariffs(db)
        return _render_lessons_paywall(request, user, course, tariffs)

    materials_result = await db.execute(
        select(Material).where(Material.lesson_id == lesson.id)
    )
    materials = materials_result.scalars().all()

    progress_result = await db.execute(
        select(LessonProgress).where(
            and_(LessonProgress.user_id == user.id, LessonProgress.lesson_id == lesson.id)
        )
    )
    progress = progress_result.scalar_one_or_none()

    raw_video_id = lesson.video_id
    has_video = bool(
        raw_video_id and str(raw_video_id).strip() and str(raw_video_id).strip() != "pending"
    )

    embed_url = None
    if has_video:
        video_service = VideoService()
        embed_url = await video_service.get_embed_link(str(raw_video_id).strip())

    if embed_url:
        import urllib.parse
        parsed = urllib.parse.urlparse(embed_url)
        params = urllib.parse.parse_qs(parsed.query)
        params["watermark"] = [user.email or str(user.id)]
        new_query = urllib.parse.urlencode(params, doseq=True)
        parts = list(parsed)
        parts[4] = new_query
        embed_url = urllib.parse.urlunparse(parts)

    course_result = await db.execute(select(Course).where(Course.id == lesson.course_id))
    course = course_result.scalar_one_or_none()
    all_lessons_result = await db.execute(
        select(Lesson).where(Lesson.course_id == lesson.course_id).order_by(Lesson.order)
    )
    all_lessons = all_lessons_result.scalars().all()

    return templates.TemplateResponse("cabinet/lesson_player.html", {
        "request": request,
        "user": user,
        "lesson": lesson,
        "course": course,
        "all_lessons": all_lessons,
        "materials": materials,
        "embed_url": embed_url,
        "has_video": has_video,
        "percent_watched": progress.percent_watched if progress else 0,
        "is_completed": progress.is_completed if progress else False,
    })


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    saved: Optional[str] = None,
    error: Optional[str] = None,
):
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=302)

    accesses = await list_user_accesses(db, user.id)

    return templates.TemplateResponse("cabinet/profile.html", {
        "request": request,
        "user": user,
        "saved": saved,
        "error": error,
        "accesses": accesses,
    })


@router.post("/profile", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(""),
):
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=302)

    user.name = name.strip() or None
    await db.commit()
    return RedirectResponse("/cabinet/profile?saved=name", status_code=303)


@router.post("/profile/avatar", response_class=HTMLResponse)
async def upload_avatar(
    request: Request,
    db: AsyncSession = Depends(get_db),
    avatar: UploadFile = File(...),
):
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=302)

    content_type = avatar.content_type or ""
    if content_type not in ALLOWED_MIME:
        return RedirectResponse("/cabinet/profile?error=bad_type", status_code=303)

    raw = await avatar.read()
    if len(raw) > AVATAR_MAX_BYTES:
        return RedirectResponse("/cabinet/profile?error=too_large", status_code=303)

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((512, 512), Image.LANCZOS)
        os.makedirs(AVATAR_DIR, exist_ok=True)
        dest = os.path.join(AVATAR_DIR, f"{user.id}.jpg")
        img.save(dest, "JPEG", quality=85)
        user.avatar_url = f"/static/uploads/avatars/{user.id}.jpg"
        await db.commit()
    except Exception:
        logger.exception("Ошибка при обработке аватара")
        return RedirectResponse("/cabinet/profile?error=upload_failed", status_code=303)

    return RedirectResponse("/cabinet/profile?saved=avatar", status_code=303)


@router.post("/profile/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return RedirectResponse("/", status_code=302)

    if new_password != confirm_password:
        return RedirectResponse("/cabinet/profile?error=password_mismatch", status_code=303)
    if len(new_password) < 8:
        return RedirectResponse("/cabinet/profile?error=password_too_short", status_code=303)
    if not user.check_password(current_password):
        return RedirectResponse("/cabinet/profile?error=wrong_password", status_code=303)

    user.set_password(new_password)
    await db.commit()
    return RedirectResponse("/cabinet/profile?saved=password", status_code=303)
