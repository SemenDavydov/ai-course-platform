import asyncio
import logging
from html import escape

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import PRODUCTION, TelegramAPIServer
import aiohttp
from app.bot.httpx_session import HttpxSession
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.course import Course, Lesson, Module, Tariff
from app.models.payment import Payment
from app.services.auth import create_login_token
from app.services.payment import PaymentService
from app.services.access import (
    TRAINING_START_LABEL,
    course_lessons_locked,
    get_primary_course,
    list_accessible_courses,
    user_has_course,
    get_access,
)

_warmup_shown: set[int] = set()

session = AiohttpSession(
    timeout=aiohttp.ClientTimeout(total=60, connect=30)
)

# Legacy lesson labels (old course only, by lesson id)
LESSON_DATA = {
    1: ("🎯", "НАЧАЛО", "Начало: подготовка к работе с сервисом"),
    2: ("💻", "ЛЕКЦИЯ 1", "Лекция 1: Написание сценария. Правила и реализация проекта"),
    3: ("🎨", "ЛЕКЦИЯ 2", "Лекция 2: Создание раскадровок для последующей анимации"),
    4: ("📽️", "ЛЕКЦИЯ 3", "Лекция 3: Анимация раскадровок и озвучка реплик персонажей"),
    5: ("🎬", "ЛЕКЦИЯ 4", "Лекция 4: Монтаж целостного видео с помощью CapCut"),
    7: ("❌", "ОШИБКИ НОВИЧКОВ", "Ошибки новичков: чего стоит избегать на начальных этапах"),
    8: ("🤳🏻", "ПРАВИЛА ПРОМТА", "Правила хорошего промта"),
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

telegram_api = (
    TelegramAPIServer.from_base(settings.TELEGRAM_API_BASE.rstrip("/"))
    if settings.TELEGRAM_API_BASE
    else PRODUCTION
)

bot = Bot(
    token=settings.BOT_TOKEN,
    session=HttpxSession(api=telegram_api),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


class Form(StatesGroup):
    waiting_for_email = State()
    waiting_tariff = State()


class DBSessionMiddleware:
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as session:
            data["db"] = session
            return await handler(event, data)


dp.message.middleware(DBSessionMiddleware())
dp.callback_query.middleware(DBSessionMiddleware())


async def get_or_create_user(telegram_id: int, db: AsyncSession, **kwargs) -> User:
    query = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=kwargs.get("username"),
            first_name=kwargs.get("first_name"),
            last_name=kwargs.get("last_name"),
            has_access=False,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("Created new user: %s", telegram_id)

    return user


def _html(value) -> str:
    return escape(str(value or ""), quote=False)


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None, **kwargs) -> None:
    """Показывает экран. Если править старое сообщение нельзя — шлёт новое."""
    shown = False
    message = callback.message
    if message is not None and hasattr(message, "edit_text"):
        try:
            await message.edit_text(text, reply_markup=reply_markup, **kwargs)
            shown = True
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                shown = True
            else:
                logger.warning("edit_text failed: %s", exc)
        except Exception:
            logger.exception("edit_text failed")
    if not shown:
        try:
            await callback.bot.send_message(
                callback.from_user.id, text, reply_markup=reply_markup, **kwargs
            )
            shown = True
        except Exception:
            logger.exception("send_message fallback failed")
    try:
        if shown:
            await callback.answer()
        else:
            await callback.answer(
                "Не удалось открыть раздел. Отправьте /start",
                show_alert=True,
            )
    except Exception:
        logger.debug("callback.answer failed", exc_info=True)


def _main_menu_keyboard(has_any_access: bool, has_story: bool, has_legacy: bool):
    rows = []
    if has_story:
        rows.append([InlineKeyboardButton(text="📖 AI STORY", callback_data="course_story")])
    if has_legacy:
        rows.append(
            [InlineKeyboardButton(text="📚 Классический курс", callback_data="course_legacy")]
        )
    if has_any_access:
        rows.append([InlineKeyboardButton(text="🌐 Войти на сайт", callback_data="site_login")])
    rows.append([InlineKeyboardButton(text="ℹ️ О курсе AI STORY", callback_data="about")])
    if not has_story:
        rows.append([InlineKeyboardButton(text="💰 Купить AI STORY", callback_data="buy")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def cmd_start(message: Message, db: AsyncSession):
    query = select(User).where(User.telegram_id == message.from_user.id)
    result = await db.execute(query)
    existing_user = result.scalar_one_or_none()

    user = await get_or_create_user(
        message.from_user.id,
        db,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    accessible = await list_accessible_courses(db, user.id)
    has_story = any(c.slug == "ai-story" for c in accessible)
    has_legacy = any(c.is_legacy for c in accessible)
    has_any = bool(accessible)

    if has_any:
        await message.answer(
            f"👋 С возвращением, {message.from_user.first_name}!\n\n"
            "✅ У вас есть доступ к курсам.\n"
            "AI STORY — на первом месте; классический курс — в меню.",
            reply_markup=_main_menu_keyboard(has_any, has_story, has_legacy),
        )
        return

    show_warmup = message.from_user.id not in _warmup_shown
    if show_warmup:
        _warmup_shown.add(message.from_user.id)

    if show_warmup:
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            "Меня зовут Елизавета Давыдова. Курс <b>AI STORY: воплоти свою историю</b> — "
            "путь от идеи вирусного ИИ-сериала до первых клиентов."
        )
        await asyncio.sleep(1.2)
        await message.answer(
            "Внутри — 7 модулей: профессия, сценарии, герой, генерация, монтаж, "
            "коммерция и монетизация.\n\n"
            "Два тарифа: <b>Pro</b> и <b>VIP</b> — доступ к материалам бессрочный."
        )
        await asyncio.sleep(1.0)

    await message.answer(
        "Хочешь узнать подробнее или сразу выбрать тариф? 👇",
        reply_markup=_main_menu_keyboard(False, False, False),
    )


async def _send_site_login_link(chat_message: Message, telegram_id: int, db: AsyncSession) -> None:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if not user or not user.has_access:
        await chat_message.answer(
            "Доступ к курсу не найден. Если вы покупали курс — напишите нам, поможем."
        )
        return

    token = await create_login_token(db, user)
    login_url = f"{settings.SITE_URL}/auth/telegram-login?token={token}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌐 Открыть личный кабинет", url=login_url)]]
    )
    await chat_message.answer(
        "🔐 Ссылка для входа в личный кабинет.\n\n"
        "Действует 15 минут и только один раз.\n"
        "Если понадобится снова — /site",
        reply_markup=keyboard,
    )


@dp.callback_query(lambda c: c.data == "site_login")
async def process_site_login(callback: CallbackQuery, db: AsyncSession):
    await _send_site_login_link(callback.message, callback.from_user.id, db)
    await callback.answer()


@dp.message(Command("site"))
async def cmd_site(message: Message, db: AsyncSession):
    await _send_site_login_link(message, message.from_user.id, db)


@dp.callback_query(lambda c: c.data == "about")
async def process_about(callback: CallbackQuery, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    course = await get_primary_course(db)
    existing = await get_access(db, user.id, course.id) if course else None
    rows = []
    if course:
        lines = [f"<b>{_html(course.title)}</b>\n", _html(course.description), ""]
        if existing:
            lines.append(f"Ваш тариф: <b>{_html(existing.tariff_slug).upper()}</b>")
            lines.append(f"Старт обучения {TRAINING_START_LABEL}.")
            rows.append([InlineKeyboardButton(text="📖 К курсу", callback_data="course_story")])
        else:
            t_result = await db.execute(
                select(Tariff)
                .where(Tariff.course_id == course.id, Tariff.is_active == True)
                .order_by(Tariff.sort_order)
            )
            for t in t_result.scalars().all():
                old = f" <s>{int(t.old_price)}₽</s>" if t.old_price else ""
                lines.append(f"• <b>{_html(t.name)}</b> — {int(t.price)}₽{old}")
            lines.append("\nСтарт обучения " + TRAINING_START_LABEL + ".")
            rows.append([InlineKeyboardButton(text="💰 Выбрать тариф", callback_data="buy")])
        about_text = "\n".join(lines)[:4000]
    else:
        about_text = "Информация о курсе скоро появится!"
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")])
    await _safe_edit(callback, about_text, InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(lambda c: c.data == "buy")
async def process_buy(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    course = await get_primary_course(db)

    if not course:
        await _safe_edit(
            callback,
            "Курс временно недоступен.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ]
            ),
        )
        return

    existing = await get_access(db, user.id, course.id)
    if existing:
        await _safe_edit(
            callback,
            f"✅ Курс уже куплен, тариф <b>{_html(existing.tariff_slug).upper()}</b>.\n\n"
            f"Старт обучения {TRAINING_START_LABEL}.\n"
            "Повторно покупать не нужно — уроки откроются в это время.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📖 К курсу", callback_data="course_story")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")],
                ]
            ),
        )
        return

    if not user.accepted_offer:
        offer_text = (
            "<b>Для оплаты примите условия</b>\n\n"
            f'<a href="{settings.SITE_URL}/offer">Публичная оферта</a>\n'
            f'<a href="{settings.SITE_URL}/privacy">Согласие на ПД</a>'
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Оферта", url=f"{settings.SITE_URL}/offer")],
                [InlineKeyboardButton(text="✅ Принимаю", callback_data="accept_offer")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")],
            ]
        )
        await _safe_edit(
            callback, offer_text, keyboard, disable_web_page_preview=True
        )
        return

    t_result = await db.execute(
        select(Tariff)
        .where(Tariff.course_id == course.id, Tariff.is_active == True)
        .order_by(Tariff.sort_order)
    )
    tariffs = list(t_result.scalars().all())
    rows = []
    for t in tariffs:
        if existing and existing.tariff_slug == "pro" and t.slug == "pro":
            continue
        label = f"{t.name} — {int(t.price)}₽"
        if t.old_price:
            label += f" (было {int(t.old_price)}₽)"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"tariff_{t.slug}")]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")])
    if not rows[:-1]:
        await _safe_edit(
            callback,
            "Сейчас нет доступных тарифов для покупки.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ]
            ),
        )
        return
    await _safe_edit(
        callback,
        "Выберите тариф AI STORY:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(lambda c: c.data == "accept_offer")
async def process_accept_offer(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    user.accepted_offer = True
    await db.commit()
    await process_buy(callback, state, db)


@dp.callback_query(lambda c: c.data and c.data.startswith("tariff_"))
async def process_tariff(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    tariff_slug = callback.data.split("_", 1)[1]
    await state.update_data(tariff_slug=tariff_slug)
    user = await get_or_create_user(callback.from_user.id, db)

    if not user.email:
        await state.set_state(Form.waiting_for_email)
        await _safe_edit(
            callback,
            "📧 Укажите email для чека:",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Отмена", callback_data="back_to_start")]
                ]
            ),
        )
        return

    course = await get_primary_course(db)
    await create_payment_and_send(callback.message, user, course, tariff_slug, db)
    try:
        await callback.answer()
    except Exception:
        pass


@dp.message(Form.waiting_for_email)
async def process_email(message: Message, state: FSMContext, db: AsyncSession):
    email = message.text.strip()
    if "@" not in email or "." not in email:
        await message.answer("Введите корректный email (name@domain.ru):")
        return

    user = await get_or_create_user(message.from_user.id, db)
    user.email = email
    await db.commit()

    data = await state.get_data()
    tariff_slug = data.get("tariff_slug", "pro")
    course = await get_primary_course(db)
    if not course:
        await message.answer("Курс временно недоступен.")
        await state.clear()
        return

    await create_payment_and_send(message, user, course, tariff_slug, db)
    await state.clear()


async def create_payment_and_send(
    message: types.Message,
    user: User,
    course: Course,
    tariff_slug: str,
    db: AsyncSession,
):
    if not course:
        await message.answer("Курс временно недоступен.")
        return

    t_result = await db.execute(
        select(Tariff).where(
            Tariff.course_id == course.id,
            Tariff.slug == tariff_slug,
            Tariff.is_active == True,
        )
    )
    tariff = t_result.scalar_one_or_none()
    if not tariff:
        await message.answer("Тариф не найден.")
        return

    payment_service = PaymentService()
    description = f"Оплата «{course.title}» — {tariff.name}"
    payment_data = await payment_service.create_payment(
        user=user,
        amount=tariff.price,
        description=description,
        course_id=course.id,
        return_url=f"https://t.me/{settings.BOT_USERNAME}",
        tariff_slug=tariff.slug,
        tariff_id=tariff.id,
    )
    if not payment_data:
        await message.answer("Ошибка создания платежа. Попробуйте позже.")
        return

    payment = Payment(
        user_id=user.id,
        amount=tariff.price,
        payment_id=payment_data["payment_id"],
        status="pending",
        description=description,
        course_id=course.id,
        tariff_id=tariff.id,
        tariff_slug=tariff.slug,
    )
    db.add(payment)
    await db.commit()

    payment_text = (
        f"💳 <b>Оплата {_html(tariff.name)}</b>\n\n"
        f"Сумма: {int(tariff.price)}₽\n\n"
        f"После оплаты доступ откроется автоматически.\n"
        f"Старт обучения {TRAINING_START_LABEL}."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_data["confirmation_url"])],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")],
        ]
    )
    try:
        await message.answer(payment_text, reply_markup=keyboard)
    except Exception:
        await bot.send_message(user.telegram_id, payment_text, reply_markup=keyboard)


async def _show_story_modules(callback: CallbackQuery, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    course = await get_primary_course(db)
    if not course or not await user_has_course(db, user, course.id):
        await _safe_edit(
            callback,
            "Нет доступа к AI STORY.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Купить", callback_data="buy")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")],
                ]
            ),
        )
        return

    m_result = await db.execute(
        select(Module).where(Module.course_id == course.id).order_by(Module.order)
    )
    modules = list(m_result.scalars().all())
    text = f"<b>{_html(course.title)}</b>\n\n"
    if course_lessons_locked(course):
        text += f"Старт обучения {TRAINING_START_LABEL}.\nМодули уже видны, уроки откроются в это время.\n\n"
    text += "Выберите модуль:"
    rows = [
        [
            InlineKeyboardButton(
                text=m.button_label or f"Модуль {m.order}",
                callback_data=f"module_{m.id}",
            )
        ]
        for m in modules
    ]
    accessible = await list_accessible_courses(db, user.id)
    if any(c.is_legacy for c in accessible):
        rows.append(
            [InlineKeyboardButton(text="📚 Классический курс", callback_data="course_legacy")]
        )
    rows.append([InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_start")])
    await _safe_edit(callback, text, InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(lambda c: c.data in ("course", "course_story"))
async def process_course_story(callback: CallbackQuery, db: AsyncSession):
    await _show_story_modules(callback, db)


@dp.callback_query(lambda c: c.data == "course_legacy")
async def process_course_legacy(callback: CallbackQuery, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    result = await db.execute(
        select(Course).where(Course.is_legacy == True, Course.is_published == True).limit(1)
    )
    course = result.scalar_one_or_none()
    if not course or not await user_has_course(db, user, course.id):
        await _safe_edit(
            callback,
            "Нет доступа к классическому курсу.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ]
            ),
        )
        return

    lessons_result = await db.execute(
        select(Lesson).where(Lesson.course_id == course.id).order_by(Lesson.order)
    )
    lessons = lessons_result.scalars().all()
    text = f"<b>{_html(course.title)}</b>\n\n"
    buttons = []
    for lesson in lessons:
        if lesson.id in LESSON_DATA:
            emoji, button_text, full_title = LESSON_DATA[lesson.id]
            text += f"{emoji} {_html(full_title)}\n\n"
            buttons.append(
                [InlineKeyboardButton(text=button_text, callback_data=f"lesson_{lesson.id}")]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"Урок {lesson.order}",
                        callback_data=f"lesson_{lesson.id}",
                    )
                ]
            )
    buttons.append([InlineKeyboardButton(text="📖 AI STORY", callback_data="course_story")])
    buttons.append([InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_start")])
    await _safe_edit(callback, text[:4000], InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(lambda c: c.data and c.data.startswith("module_"))
async def process_module(callback: CallbackQuery, db: AsyncSession):
    module_id = int(callback.data.split("_")[1])
    user = await get_or_create_user(callback.from_user.id, db)
    module = await db.get(Module, module_id)
    if not module:
        await _safe_edit(callback, "Модуль не найден")
        return
    if not await user_has_course(db, user, module.course_id):
        await _safe_edit(callback, "Нет доступа")
        return

    course = await db.get(Course, module.course_id)
    lessons_result = await db.execute(
        select(Lesson).where(Lesson.module_id == module.id).order_by(Lesson.order)
    )
    lessons = list(lessons_result.scalars().all())
    locked = course_lessons_locked(course)
    text = f"<b>Модуль {module.order}. {_html(module.title)}</b>\n\n"
    if locked:
        text += f"Уроки откроются {TRAINING_START_LABEL}.\n\n"
    for i, lesson in enumerate(lessons, start=1):
        prefix = "🔒 " if locked else ""
        text += f"{prefix}Урок {i}. {_html(lesson.title)}\n"
    rows = [
        [
            InlineKeyboardButton(
                text=(f"🔒 Урок {i}" if locked else f"Урок {i}"),
                callback_data=f"lesson_{lesson.id}",
            )
        ]
        for i, lesson in enumerate(lessons, start=1)
    ]
    rows.append([InlineKeyboardButton(text="◀️ К модулям", callback_data="course_story")])
    await _safe_edit(callback, text[:4000], InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(lambda c: c.data and c.data.startswith("lesson_"))
async def process_lesson(callback: CallbackQuery, db: AsyncSession):
    lesson_id = int(callback.data.split("_")[1])
    lesson = await db.get(Lesson, lesson_id)
    if not lesson:
        await _safe_edit(callback, "❌ Урок не найден")
        return

    user = await get_or_create_user(callback.from_user.id, db)
    if not await user_has_course(db, user, lesson.course_id):
        await _safe_edit(callback, "❌ Нет доступа к этому курсу")
        return

    course = await db.get(Course, lesson.course_id)
    back_cb = "course_legacy" if course and course.is_legacy else (
        f"module_{lesson.module_id}" if lesson.module_id else "course_story"
    )
    keyboard_rows = [[InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb)]]

    if course_lessons_locked(course):
        await _safe_edit(
            callback,
            f"🔒 <b>{_html(lesson.title)}</b>\n\n"
            f"Урок откроется {TRAINING_START_LABEL}.",
            InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        )
        return

    if course and course.is_legacy and lesson_id in LESSON_DATA:
        emoji, _, full_title = LESSON_DATA[lesson_id]
        title = f"{emoji} {_html(full_title)}"
    else:
        title = _html(lesson.title)

    text = f"<b>{title}</b>\n\n{_html(lesson.description)}"

    vid = (lesson.video_id or "").strip()
    if vid and vid != "pending":
        display_url = f"https://kinescope.io/{vid}"
        text += f"\n\n🔗 Ссылка на видео:\n<code>{_html(display_url)}</code>"
        keyboard_rows.insert(
            0, [InlineKeyboardButton(text="▶️ Смотреть видео", url=display_url)]
        )
    else:
        text += "\n\n<i>Видео скоро появится.</i>"

    await _safe_edit(
        callback,
        text[:4000],
        InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )


@dp.callback_query(lambda c: c.data == "back_to_start")
async def process_back_to_start(callback: CallbackQuery, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    accessible = await list_accessible_courses(db, user.id)
    has_story = any(c.slug == "ai-story" for c in accessible)
    has_legacy = any(c.is_legacy for c in accessible)
    has_any = bool(accessible)

    text = f"👋 Привет, {_html(callback.from_user.first_name)}!\n\n"
    if has_any:
        text += "Выберите курс или откройте кабинет на сайте."
    else:
        text += "Узнайте о AI STORY или выберите тариф."

    await _safe_edit(
        callback,
        text,
        _main_menu_keyboard(has_any, has_story, has_legacy),
    )


@dp.error()
async def on_bot_error(event):
    logger.exception("Bot handler error: %s", event.exception)
    update = event.update
    callback = getattr(update, "callback_query", None)
    if callback is not None:
        try:
            await callback.answer(
                "Что-то пошло не так. Нажмите /start",
                show_alert=True,
            )
        except Exception:
            pass
    return True


async def start_bot():
    logger.info("Starting bot...")
    retries = 1
    for i in range(retries):
        try:
            await dp.start_polling(bot, polling_timeout=30, limit=5)
            break
        except Exception as e:
            logger.error("Polling failed (attempt %s/%s): %s", i + 1, retries, e)
            if i < retries - 1:
                await asyncio.sleep(5)
            else:
                raise


def run_bot():
    asyncio.run(start_bot())


if __name__ == "__main__":
    if not settings.BOT_ENABLED:
        print("Bot disabled (BOT_ENABLED=false). Exiting.")
        import sys

        sys.exit(0)
    run_bot()

