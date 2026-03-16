from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Экспресс курс по созданию ИИ анимаций и изображений"
    DEBUG: bool = False
    SECRET_KEY: str
    ADMIN_SECRET_CODE: str = "admin"

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

    # SMTP для отправки чеков (если нужно)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


settings = Settings()
