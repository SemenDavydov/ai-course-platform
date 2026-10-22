"""Per-course access entitlements."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course, UserCourseAccess
from app.models.payment import Payment
from app.models.user import User
from app.services.tariffs import PRO, SELF, VIP, access_expiry_for, tariff_rank

# Moscow is UTC+3 year-round (kept for any date helpers).
MSK = timezone(timedelta(hours=3))
# Историческая дата старта потока; уроки уже открыты для всех с доступом.
TRAINING_START = datetime(2026, 9, 21, 14, 0, tzinfo=MSK)
TRAINING_START_LABEL = "21.09.2026 в 14:00 МСК"


def course_lessons_locked(course: Course | None, *, now: datetime | None = None) -> bool:
    """Уроки открыты: прелок до старта потока снят."""
    return False


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def access_is_active(
    access: UserCourseAccess | None, *, now: datetime | None = None
) -> bool:
    """Доступ действует: есть запись и срок не истёк (expires_at NULL = бессрочно)."""
    if access is None:
        return False
    if access.expires_at is None:
        return True
    return _as_utc(access.expires_at) > (now or datetime.now(timezone.utc))


def _active_clause(now: datetime):
    return or_(UserCourseAccess.expires_at.is_(None), UserCourseAccess.expires_at > now)


def purchase_block_reason(
    existing: UserCourseAccess | None, tariff_slug: str
) -> str | None:
    """Почему нельзя купить tariff_slug поверх текущего доступа (None — можно).

    Общие правила для сайта (API платежей) и Telegram-бота.
    existing может быть истёкшим — истекает только self.
    """
    if not existing:
        return None

    if not access_is_active(existing):
        if tariff_slug == SELF:
            return (
                "Срок тарифа «Самостоятельный» закончился. Повторно его купить "
                "нельзя — доступны тарифы Pro и VIP."
            )
        return None

    if existing.tariff_slug == VIP:
        return "У вас уже есть VIP-доступ к этому курсу"
    if existing.tariff_slug == SELF and tariff_slug == SELF:
        until = existing.expires_at.strftime("%d.%m.%Y") if existing.expires_at else ""
        return (
            f"Доступ по тарифу «Самостоятельный» уже действует до {until}. "
            "Доступен апгрейд до Pro или VIP"
        )
    if existing.tariff_slug == tariff_slug:
        return "У вас уже есть доступ по этому тарифу"
    if existing.tariff_slug == PRO and tariff_slug != VIP:
        return "У вас уже есть Pro-доступ. Доступен апгрейд до VIP"
    if tariff_slug == SELF and existing.expires_at is None:
        # Бессрочный доступ (напр. legacy) срочным self не заменяется — деньги ушли бы впустую.
        return "У вас уже есть бессрочный доступ к этому курсу"
    return None


async def paid_credit(
    db: AsyncSession, user_id: int, course_id: int, existing: UserCourseAccess | None
) -> float:
    """Сколько уже оплачено за курс — зачитывается при переходе на тариф выше.

    Только пока доступ действует: после истечения «Самостоятельного» зачёта нет.
    Считаем успешные платежи (возвраты имеют статус refunded и не попадают).
    Доступ, выданный админом без оплаты, даёт зачёт 0.
    """
    if not access_is_active(existing):
        return 0.0
    result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
            Payment.user_id == user_id,
            Payment.course_id == course_id,
            Payment.status == "succeeded",
        )
    )
    return float(result.scalar_one() or 0.0)


def upgrade_price(tariff_price: float, credit: float) -> float:
    """Цена к оплате с учётом зачёта (не ниже нуля)."""
    return max(round(float(tariff_price) - float(credit or 0), 2), 0.0)


async def get_access(
    db: AsyncSession, user_id: int, course_id: int
) -> UserCourseAccess | None:
    """Запись доступа к курсу, В ТОМ ЧИСЛЕ истёкшая (для правил покупки/апгрейда).
    Проверять право смотреть курс — через user_has_course / access_is_active."""
    result = await db.execute(
        select(UserCourseAccess).where(
            UserCourseAccess.user_id == user_id,
            UserCourseAccess.course_id == course_id,
        )
    )
    return result.scalar_one_or_none()


async def get_active_access(
    db: AsyncSession, user_id: int, course_id: int
) -> UserCourseAccess | None:
    access = await get_access(db, user_id, course_id)
    return access if access_is_active(access) else None


async def user_has_course(db: AsyncSession, user: User, course_id: int) -> bool:
    access = await get_access(db, user.id, course_id)
    return access_is_active(access)


async def list_user_accesses(
    db: AsyncSession, user_id: int, *, include_expired: bool = False
) -> list[UserCourseAccess]:
    query = (
        select(UserCourseAccess)
        .where(UserCourseAccess.user_id == user_id)
        .options(selectinload(UserCourseAccess.course))
        .join(Course, Course.id == UserCourseAccess.course_id)
        .order_by(Course.sort_order.asc(), Course.id.asc())
    )
    if not include_expired:
        query = query.where(_active_clause(datetime.now(timezone.utc)))
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_accessible_courses(db: AsyncSession, user_id: int) -> list[Course]:
    result = await db.execute(
        select(Course)
        .join(UserCourseAccess, UserCourseAccess.course_id == Course.id)
        .where(
            UserCourseAccess.user_id == user_id,
            _active_clause(datetime.now(timezone.utc)),
        )
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
    """Создаёт или апгрейдит доступ.

    - self: срок SELF_ACCESS_DAYS с момента выдачи; pro/vip/legacy — бессрочно.
    - Апгрейд по рангу legacy < self < pro < vip; апгрейд до pro/vip снимает срок.
    - Действующий бессрочный доступ никогда не заменяется срочным (self поверх
      активного pro/vip/legacy ничего не меняет).
    - Истёкший доступ заменяется любым новым грантом.
    """
    now = datetime.now(timezone.utc)
    access = await get_access(db, user.id, course_id)
    new_expires_at = access_expiry_for(tariff_slug, now)
    if access is None:
        access = UserCourseAccess(
            user_id=user.id,
            course_id=course_id,
            tariff_slug=tariff_slug,
            granted_at=now,
            expires_at=new_expires_at,
        )
        db.add(access)
    else:
        active = access_is_active(access, now=now)
        downgrade_to_expiring = (
            active and access.expires_at is None and new_expires_at is not None
        )
        if not active:
            access.tariff_slug = tariff_slug
            access.granted_at = now
            access.expires_at = new_expires_at
            access.expiry_reminder_sent_at = None
        elif not downgrade_to_expiring and tariff_rank(tariff_slug) >= tariff_rank(
            access.tariff_slug
        ):
            access.tariff_slug = tariff_slug
            access.expires_at = new_expires_at
            access.expiry_reminder_sent_at = None

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


async def refresh_user_has_access(
    db: AsyncSession, user: User, *, now: datetime | None = None
) -> bool:
    """Пересчитывает user.has_access после истечения срока.

    Снимает флаг только если у пользователя есть записи доступа и ни одна не
    действует: у старых бот-покупателей без записей (has_access из платежа без
    course_id) флаг не трогаем.
    """
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(UserCourseAccess).where(UserCourseAccess.user_id == user.id)
    )
    accesses = list(result.scalars().all())
    if accesses and not any(access_is_active(a, now=now) for a in accesses):
        user.has_access = False
    elif any(access_is_active(a, now=now) for a in accesses):
        user.has_access = True
    return bool(user.has_access)


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
