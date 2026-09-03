"""Per-course access entitlements."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course, UserCourseAccess
from app.models.user import User


async def get_access(
    db: AsyncSession, user_id: int, course_id: int
) -> UserCourseAccess | None:
    result = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user_id,
            UserCourseAccess.course_id == course_id,
        )
    )
    return result.scalar_one_or_none()


async def user_has_course(db: AsyncSession, user: User, course_id: int) -> bool:
    access = await get_access(db, user.id, course_id)
    return access is not None


async def list_user_accesses(
    db: AsyncSession, user_id: int
) -> list[UserCourseAccess]:
    result = await db.execute(
        select(UserCourseAccess)
        .where(UserCourseAccess.user_id == user_id)
        .options(selectinload(UserCourseAccess.course))
        .join(Course, Course.id == UserCourseAccess.course_id)
        .order_by(Course.sort_order.asc(), Course.id.asc())
    )
    return list(result.scalars().all())


async def list_accessible_courses(db: AsyncSession, user_id: int) -> list[Course]:
    result = await db.execute(
        select(Course)
        .join(UserCourseAccess, UserCourseAccess.course_id == Course.id)
        .where(UserCourseAccess.user_id == user_id)
        .order_by(Course.sort_order.asc(), Course.id.asc())
    )
    return list(result.scalars().unique().all())


async def grant_course_access(
    db: AsyncSession,
    user: User,
    course_id: int,
    tariff_slug: str,
    *,
    commit: bool = False,
) -> UserCourseAccess:
    """Create or upgrade entitlement. VIP upgrades over Pro/legacy."""
    access = await get_access(db, user.id, course_id)
    rank = {"legacy": 0, "pro": 1, "vip": 2}
    if access is None:
        access = UserCourseAccess(
            user_id=user.id,
            course_id=course_id,
            tariff_slug=tariff_slug,
            granted_at=datetime.now(timezone.utc),
        )
        db.add(access)
    else:
        if rank.get(tariff_slug, 0) >= rank.get(access.tariff_slug, 0):
            access.tariff_slug = tariff_slug

    user.has_access = True
    if user.access_granted_at is None:
        user.access_granted_at = datetime.now(timezone.utc)

    if commit:
        await db.commit()
        await db.refresh(access)
    return access


async def revoke_course_access(
    db: AsyncSession, user: User, course_id: int, *, commit: bool = False
) -> None:
    access = await get_access(db, user.id, course_id)
    if access:
        await db.delete(access)

    remaining = await db.execute(
        select(UserCourseAccess.id).where(UserCourseAccess.user_id == user.id).limit(1)
    )
    if remaining.scalar_one_or_none() is None:
        user.has_access = False

    if commit:
        await db.commit()


async def get_primary_course(db: AsyncSession) -> Course | None:
    """Published non-legacy course first (AI STORY), else first published."""
    result = await db.execute(
        select(Course)
        .where(Course.is_published == True, Course.is_legacy == False)
        .order_by(Course.sort_order.asc(), Course.id.asc())
        .limit(1)
    )
    course = result.scalar_one_or_none()
    if course:
        return course
    result = await db.execute(
        select(Course)
        .where(Course.is_published == True)
        .order_by(Course.sort_order.asc(), Course.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_course_by_slug(db: AsyncSession, slug: str) -> Course | None:
    result = await db.execute(select(Course).where(Course.slug == slug))
    return result.scalar_one_or_none()
