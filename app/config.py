from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI STORY: воплоти свою историю"
    DEBUG: bool = False
    SECRET_KEY: str
    ADMIN_SECRET_CODE: str = "admin"
    CLC_API_KEY: str = ""  # API ключ для clc.li

    # Соцсети (лендинг)
    INSTAGRAM_URL: str = ""
    TIKTOK_URL: str = ""

    # Database
    DATABASE_URL: str

    # Telegram
    BOT_TOKEN: str
    BOT_USERNAME: str = "DavydovaAIBot"
    # Прокси до Telegram API (напр. Cloudflare Worker): с хостинга в РФ
    # api.telegram.org недоступен. Пусто — ходим напрямую.
    TELEGRAM_API_BASE: str = ""

    # YooKassa
    YOOKASSA_SHOP_ID: str
    YOOKASSA_SECRET_KEY: str

    # Kinescope
    KINESCOPE_API_KEY: str = ""
    KINESCOPE_PROJECT_ID: str = ""

    # Site URL - ТВОЙ ДОМЕН
    SITE_URL: str = "http://localhost:8000"  # для разработки

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Video link lifetime (seconds)
    VIDEO_LINK_LIFETIME: int = 7200  # 2 часа

    # Bot feature flag
    BOT_ENABLED: bool = False

    # Invite-ссылки в закрытые Telegram-каналы после оплаты
    PRO_CHAT_INVITE_URL: str = ""
    VIP_CHAT_INVITE_URL: str = ""

    # Уведомление админу о VIP-покупке
    ADMIN_NOTIFY_EMAIL: str = ""
    ADMIN_TELEGRAM_ID: str = ""

    # --- Webinar / lead-magnet бот (отдельный процесс) ---
    WEBINAR_BOT_TOKEN: str = ""
    WEBINAR_BOT_ENABLED: bool = False
    # Invite / ссылка на TG-канал с инфой по вебинару
    WEBINAR_CHAT_INVITE_URL: str = "https://t.me/ai_story_news"
    # Ссылка на эфир (Telemost и т.п.)
    WEBINAR_TELEMOST_URL: str = "https://telemost.yandex.ru/j/84788316089639"
    # Расписание в МСК: YYYY-MM-DDTHH:MM:SS
    WEBINAR_ANNOUNCE_AT: str = "2026-09-14T12:00:00"
    WEBINAR_REMIND_AT: str = "2026-09-15T12:00:00"
    WEBINAR_LAST_PUSH_AT: str = "2026-09-15T18:00:00"
    # Telegram ID админов через запятую (/stats, /send_now)
    WEBINAR_ADMIN_TELEGRAM_IDS: str = ""

    # Письма уходят в фоновом потоке, чтобы не задерживать HTTP-запрос.
    # False — синхронно (используется в тестах).
    EMAIL_BACKGROUND: bool = True

    # SMTP для отправки писем
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    model_config = ConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def cookie_secure(self) -> bool:
        """HTTPS-only cookies on production; off in DEBUG or when SITE_URL is not https."""
        if self.DEBUG:
            return False
        return self.SITE_URL.lower().strip().startswith("https://")


settings = Settings()
