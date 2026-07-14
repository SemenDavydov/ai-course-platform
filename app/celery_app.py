from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "ai_course",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"]
)

# Настройки Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 минут
    task_soft_time_limit=25 * 60,  # 25 минут

    # Если брокера нет (на проде Redis не установлен), .delay() должен упасть
    # сразу, а не ретраить публикацию: иначе enqueue_email уходит в фолбэк
    # только через несколько секунд, и на столько же виснет регистрация.
    broker_connection_timeout=2,
    broker_connection_max_retries=0,
    broker_connection_retry_on_startup=False,
    task_publish_retry=False,
)

# Периодические задачи (расписание)
celery_app.conf.beat_schedule = {
    "send-daily-report": {
        "task": "app.tasks.send_daily_report",
        "schedule": crontab(hour=9, minute=0),  # Каждый день в 9 утра
    },
    "check-expired-links": {
        "task": "app.tasks.cleanup_expired_links",
        "schedule": crontab(minute="*/30"),  # Каждые 30 минут
    },
}

if __name__ == "__main__":
    celery_app.start()