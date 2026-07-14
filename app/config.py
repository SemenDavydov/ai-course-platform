from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

class Settings(BaseSettings):
    # App
    APP_NAME: str = "Экспресс курс по созданию ИИ анимаций и изображений"
    DEBUG: bool = False
    SECRET_KEY: str
    ADMIN_SECRET_CODE: str = "admin"
    CLC_API_KEY: str = ""  # API ключ для clc.li

    # Database
    DATABASE_URL: str

    # Telegram
    BOT_TOKEN: str
    BOT_USERNAME: str = "DavydovaAIBot"

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
