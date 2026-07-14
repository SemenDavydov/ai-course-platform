import asyncio
import logging
import urllib.parse
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiogram.client.session.aiohttp import AiohttpSession
import aiohttp
from app.bot.httpx_session import HttpxSession
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.models.course import Course, Lesson
from app.services.auth import create_login_token
from app.services.payment import PaymentService
from app.services.video import VideoService
from app.services.shortener import URLShortener

# Users who have already seen the warmup sequence in this bot session
_warmup_shown: set[int] = set()

session = AiohttpSession(
    timeout=aiohttp.ClientTimeout(total=60, connect=30)
)

LESSON_DATA = {
    1: ("🎯", "НАЧАЛО", "Начало: подготовка к работе с сервисом"),
    2: ("💻", "ЛЕКЦИЯ 1", "Лекция 1: Написание сценария. Правила и реализация проекта"),
    3: ("🎨", "ЛЕКЦИЯ 2", "Лекция 2: Создание раскадровок для последующей анимации"),
    4: ("📽️", "ЛЕКЦИЯ 3", "Лекция 3: Анимация раскадровок и озвучка реплик персонажей"),
    5: ("🎬", "ЛЕКЦИЯ 4", "Лекция 4: Монтаж целостного видео с помощью CapCut"),
    7: ("❌", "ОШИБКИ НОВИЧКОВ", "Ошибки новичков: чего стоит избегать на начальных этапах"),
    8: ("🤳🏻", "ПРАВИЛА ПРОМТА", "Правила хорошего промта"),
}

DEFAULT_LESSON_EMOJI = ("📹", "Урок")

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(
    token=settings.BOT_TOKEN,
    session=HttpxSession(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# Состояния для FSM (если понадобятся)
class Form(StatesGroup):
    waiting_for_email = State()


# Middleware для получения сессии БД
class DBSessionMiddleware:
    async def __call__(self, handler, event, data):
        async with AsyncSessionLocal() as session:
            data['db'] = session
            return await handler(event, data)


dp.message.middleware(DBSessionMiddleware())
dp.callback_query.middleware(DBSessionMiddleware())


# Вспомогательные функции
async def get_or_create_user(telegram_id: int, db: AsyncSession, **kwargs) -> User:
    """Получает или создает пользователя"""
    query = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=kwargs.get('username'),
            first_name=kwargs.get('first_name'),
            last_name=kwargs.get('last_name'),
            has_access=False
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"Created new user: {telegram_id}")

    return user


# Команда /start
@dp.message(CommandStart())
async def cmd_start(message: Message, db: AsyncSession):
    """Обработчик команды /start"""
    # Проверяем, новый ли пользователь
    query = select(User).where(User.telegram_id == message.from_user.id)
    result = await db.execute(query)
    existing_user = result.scalar_one_or_none()
    is_new = existing_user is None

    user = await get_or_create_user(
        message.from_user.id,
        db,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )

    # Для пользователей с доступом — сразу показываем курс
    if user.has_access:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Войти на сайт", callback_data="site_login")],
                [InlineKeyboardButton(text="📖 Перейти к курсу", callback_data="course")],
                [InlineKeyboardButton(text="📚 О курсе", callback_data="about")],
            ]
        )
        await message.answer(
            f"👋 С возвращением, {message.from_user.first_name}!\n\n"
            "✅ У вас есть полный доступ к курсу.\n\n"
            "🌐 Теперь курс доступен и на сайте — в личном кабинете. "
            "Нажмите «Войти на сайт», доступ сохранится.",
            reply_markup=keyboard
        )
        return

    # Прогревающая последовательность — для новых или тех, кто ещё не видел в этой сессии
    show_warmup = message.from_user.id not in _warmup_shown
    if show_warmup:
        _warmup_shown.add(message.from_user.id)

    if show_warmup:
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            "Меня зовут Елизавета Давыдова — я создаю ИИ-анимации "
            "и обучаю этому других. Рада видеть тебя здесь! 🎉"
        )
        await asyncio.sleep(1.5)

        await message.answer(
            "🎬 <b>Представь:</b> ты сам создаёшь анимационные ролики для соцсетей — "
            "без дизайнеров, без сложных программ, прямо с телефона.\n\n"
            "Именно это я покажу тебе в экспресс-курсе по созданию ИИ-анимаций и изображений."
        )
        await asyncio.sleep(2)

        await message.answer(
            "В курсе ты научишься:\n\n"
            "🐳 Писать сценарии с помощью <b>DeepSeek</b>\n"
            "💻 Генерировать изображения и анимации в <b>Grok</b>\n"
            "📽️ Монтировать ролики в <b>CapCut</b>\n"
            "🎶 Добавлять закадровую озвучку через <b>Zvukogram</b>\n\n"
            "Всё — пошагово, с видеоуроками и готовыми материалами 📂"
        )
        await asyncio.sleep(1.5)

    # Главное меню (для новых — после прогрева, для вернувшихся — сразу)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 О курсе", callback_data="about")],
            [InlineKeyboardButton(text="💰 Купить доступ", callback_data="buy")],
        ]
    )

    if show_warmup:
        menu_text = (
            "Хочешь узнать подробнее или сразу приступить? 👇"
        )
    else:
        menu_text = (
            f"👋 Привет, {message.from_user.first_name}!\n\n"
            "Для получения доступа к курсу нажми «Купить доступ»."
        )

    await message.answer(menu_text, reply_markup=keyboard)


async def _send_site_login_link(chat_message: Message, telegram_id: int, db: AsyncSession) -> None:
    """
    Выдаёт одноразовую ссылку для входа в личный кабинет на сайте.
    Главный путь миграции для покупателей, у которых в базе нет email.
    """
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
        "Действует 15 минут и только один раз — никому её не передавайте.\n"
        "Если понадобится снова — отправьте команду /site.",
        reply_markup=keyboard,
    )


@dp.callback_query(lambda c: c.data == "site_login")
async def process_site_login(callback: CallbackQuery, db: AsyncSession):
    """Кнопка «Войти на сайт»."""
    await _send_site_login_link(callback.message, callback.from_user.id, db)
    await callback.answer()


@dp.message(Command("site"))
async def cmd_site(message: Message, db: AsyncSession):
    """Команда /site — получить ссылку на личный кабинет."""
    await _send_site_login_link(message, message.from_user.id, db)


# Обработчик кнопки "О курсе"
@dp.callback_query(lambda c: c.data == "about")
async def process_about(callback: CallbackQuery, db: AsyncSession):
    """Показывает информацию о курсе"""
    # Сразу отвечаем на callback
    await callback.answer()

    # Получаем информацию о курсе из БД
    query = select(Course).where(Course.is_published == True)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if course:
        about_text = (
            f"📚 *{course.title}*\n\n"
            f"{course.description}\n\n"
            f"💰 Стоимость: {course.price}₽\n\n"
            f"✅ После покупки доступ открывается автоматически и действует бессрочно!"
        )
    else:
        about_text = "Информация о курсе скоро появится!"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Купить курс", callback_data="buy")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
        ]
    )

    await callback.message.edit_text(about_text, reply_markup=keyboard, parse_mode="Markdown")


# Обработчик кнопки "Купить"
@dp.callback_query(lambda c: c.data == "buy")
async def process_buy(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    """Начинает процесс покупки"""

    user = await get_or_create_user(callback.from_user.id, db)

    # Если у пользователя уже есть доступ
    if user.has_access:
        await callback.message.edit_text(
            "✅ У вас уже есть доступ к курсу!\n\n"
            "Нажмите кнопку ниже, чтобы перейти к обучению.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📖 Перейти к курсу", callback_data="course")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ]
            )
        )
        await callback.answer()
        return

    # Получаем курс
    query = select(Course).where(Course.is_published == True)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if not course:
        await callback.message.edit_text(
            "К сожалению, курс временно недоступен для покупки. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ]
            )
        )
        await callback.answer()
        return

    if not user.accepted_offer:
        offer_text = (
            "*Для продолжения оплаты необходимо принять условия*\n\n"
            "Пожалуйста, внимательно ознакомьтесь с документами:\n"
            f"🔗 [Публичная оферта]({settings.SITE_URL}/offer)\n"
            f"🔗 [Согласие на обработку ПД]({settings.SITE_URL}/privacy)\n\n"
            "Нажимая «✅ Принимаю», вы подтверждаете, что прочитали и согласны с условиями."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Открыть оферту", url=f"{settings.SITE_URL}/offer")],
                [InlineKeyboardButton(text="📄 Согласие на ПД", url=f"{settings.SITE_URL}/privacy")],
                [InlineKeyboardButton(text="✅ Принимаю", callback_data="accept_offer")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
            ]
        )
        await callback.message.edit_text(offer_text, reply_markup=keyboard, parse_mode="Markdown", disable_web_page_preview=True)
        await callback.answer()
        return

    # Если у пользователя нет email, запрашиваем его (нужен для чеков)
    if not user.email:
        await callback.message.edit_text(
            "📧 Для оформления покупки пожалуйста, укажите ваш email:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Отмена", callback_data="back_to_start")]
                ]
            )
        )
        await state.set_state(Form.waiting_for_email)
        await callback.answer()
        return

    # Если email уже есть, создаем платеж
    await create_payment_and_send(callback.message, user, course, db)
    await callback.answer()


@dp.callback_query(lambda c: c.data == "accept_offer")
async def process_accept_offer(callback: CallbackQuery, state: FSMContext, db: AsyncSession):
    user = await get_or_create_user(callback.from_user.id, db)
    user.accepted_offer = True
    await db.commit()

    # process_buy сам вызовет callback.answer()
    await process_buy(callback, state, db)

# Обработчик ввода email
@dp.message(Form.waiting_for_email)
async def process_email(message: Message, state: FSMContext, db: AsyncSession):
    """Сохраняет email пользователя и создает платеж"""

    email = message.text.strip()

    # Простая валидация email
    if '@' not in email or '.' not in email:
        await message.answer(
            "Пожалуйста, введите корректный email (например, name@domain.ru):"
        )
        return

    # Получаем пользователя
    user = await get_or_create_user(message.from_user.id, db)

    # Сохраняем email
    user.email = email
    await db.commit()

    # Получаем курс
    query = select(Course).where(Course.is_published == True)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if not course:
        await message.answer("Курс временно недоступен.")
        await state.clear()
        return

    # Создаем платеж
    await create_payment_and_send(message, user, course, db)
    await state.clear()


async def create_payment_and_send(message: types.Message, user: User, course: Course, db: AsyncSession):
    """Создает платеж и отправляет ссылку на оплату"""

    # Создаем платеж через ЮKassa
    payment_service = PaymentService()
    payment_data = await payment_service.create_payment(
        user=user,
        amount=course.price,
        description=f"Оплата курса {course.title}",
        course_id=course.id,
        return_url=f"https://t.me/{settings.BOT_USERNAME}"
    )

    if not payment_data:
        await message.answer(
            "Произошла ошибка при создании платежа. Пожалуйста, попробуйте позже."
        )
        return

    # Сохраняем информацию о платеже в БД
    from app.models.payment import Payment
    payment = Payment(
        user_id=user.id,
        amount=course.price,
        payment_id=payment_data['payment_id'],
        status="pending",
        description=f"Оплата курса {course.title}"
    )
    db.add(payment)
    await db.commit()

    # Отправляем пользователю ссылку на оплату
    payment_text = (
        f"💳 *Оплата курса*\n\n"
        f"Сумма: {course.price}₽\n\n"
        f"Для завершения покупки нажмите кнопку ниже и оплатите через любой удобный способ:\n"
        f"• Картой РФ\n"
        f"• СБП (Система быстрых платежей)\n"
        f"• ЮMoney и другие\n\n"
        f"После оплаты доступ откроется автоматически!"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_data['confirmation_url'])],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
        ]
    )

    await message.answer(payment_text, reply_markup=keyboard, parse_mode="Markdown")


# Обработчик кнопки "Курс"
@dp.callback_query(lambda c: c.data == "course")
async def process_course(callback: CallbackQuery, db: AsyncSession):
    """Показывает содержание курса"""
    await callback.answer()

    user = await get_or_create_user(callback.from_user.id, db)

    if not user.has_access:
        await callback.message.edit_text(
            "❌ У вас нет доступа к курсу.\n\n"
            "Для получения доступа необходимо приобрести курс.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Купить курс", callback_data="buy")],
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_start")]
                ]
            )
        )
        return

    query = select(Course).where(Course.is_published == True)
    result = await db.execute(query)
    course = result.scalar_one_or_none()

    if not course:
        await callback.message.edit_text("Курс временно недоступен.")
        return

    # Загружаем уроки
    lessons_query = select(Lesson).where(Lesson.course_id == course.id).order_by(Lesson.order)
    lessons_result = await db.execute(lessons_query)
    lessons = lessons_result.scalars().all()

    # Формируем текст с названием курса
    text = f"📚 *{course.title}*\n\n"

    # Добавляем каждый урок с эмодзи
    for lesson in lessons:
        if lesson.id in LESSON_DATA:
            emoji, _, full_title = LESSON_DATA[lesson.id]
            text += f"{emoji} *{full_title}*\n\n"

    # Создаём кнопки
    buttons = []
    for lesson in lessons:
        if lesson.id in LESSON_DATA:
            emoji, button_text, _ = LESSON_DATA[lesson.id]
            buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"lesson_{lesson.id}")])

    buttons.append([InlineKeyboardButton(text="◀️ НАЗАД", callback_data="back_to_start")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


# Обработчик выбора урока
@dp.callback_query(lambda c: c.data.startswith("lesson_"))
async def process_lesson(callback: CallbackQuery, db: AsyncSession):
    """Показывает содержимое урока"""
    await callback.answer()

    lesson_id = int(callback.data.split("_")[1])

    query = select(Lesson).where(Lesson.id == lesson_id)
    result = await db.execute(query)
    lesson = result.scalar_one_or_none()

    if not lesson:
        await callback.message.edit_text("❌ Урок не найден")
        return

    user = await get_or_create_user(callback.from_user.id, db)

    if not user.has_access:
        await callback.message.edit_text(
            "❌ У вас нет доступа к курсу",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="course")]
                ]
            )
        )
        return

    # Получаем данные урока из словаря
    emoji, _, full_title = LESSON_DATA.get(lesson_id, ("📹", "УРОК", "Урок"))

    text = f"{emoji} *{full_title}*\n\n{lesson.description}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К УРОКАМ", callback_data="course")]
        ]
    )

    if lesson.video_id and lesson.video_id.strip():
        # Временно: прямая ссылка на Kinescope (пока Timeweb лежит)
        display_url = f"https://kinescope.io/{lesson.video_id}"

        text = (
            f"{emoji} *{full_title}*\n\n"
            f"{lesson.description}\n\n"
            f"🔗 *Ссылка на видео:*\n"
            f"`{display_url}`\n\n"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="▶️ СМОТРЕТЬ ВИДЕО", url=display_url)],
                [InlineKeyboardButton(text="◀️ К УРОКАМ", callback_data="course")]
            ]
        )

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


# Обработчик кнопки "Назад"
@dp.callback_query(lambda c: c.data == "back_to_start")
async def process_back_to_start(callback: CallbackQuery, db: AsyncSession):
    """Возвращает в главное меню"""
    # Сразу отвечаем на callback, чтобы избежать таймаута
    await callback.answer()

    # Теперь выполняем основную логику
    user = await get_or_create_user(callback.from_user.id, db)

    # Приветственное сообщение
    welcome_text = (
        f"👋 Привет, {callback.from_user.first_name}!\n\n"
        f"Для того, чтобы узнать подробнее о курсе, нажми на кнопку 'О курсе'.\n\n"
    )

    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 О курсе", callback_data="about")],
            [InlineKeyboardButton(text="💰 Купить доступ", callback_data="buy")],
        ]
    )

    # Если у пользователя уже есть доступ, добавляем кнопку перехода к курсу
    if user.has_access:
        welcome_text += "У вас уже есть доступ к курсу!"
        keyboard.inline_keyboard.insert(
            0,
            [InlineKeyboardButton(text="📖 Перейти к курсу", callback_data="course")]
        )
    else:
        welcome_text += "💡 Для получения доступа необходимо приобрести курс."

    await callback.message.edit_text(welcome_text, reply_markup=keyboard)


# Запуск бота
async def start_bot():
    """Запускает бота с повторными попытками"""
    logger.info("Starting bot...")
    retries = 1
    for i in range(retries):
        try:
            await dp.start_polling(bot, polling_timeout=30, limit=5)
            break
        except Exception as e:
            logger.error(f"Polling failed (attempt {i+1}/{retries}): {e}")
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
