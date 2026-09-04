// PM2-конфиг прода. Запуск: pm2 start ecosystem.config.js
//
// Celery-процессов здесь нет: на сервере не установлен Redis, а без брокера
// воркер только крутился бы в рестартах. Письма при недоступном брокере
// уходят синхронно (см. enqueue_email в app/tasks.py). Если позже поднимете
// Redis — верните сюда celery-worker и celery-beat.
module.exports = {
  apps: [
    {
      name: "fastapi",
      script: "/root/ai-course-platform/venv/bin/uvicorn",
      // --proxy-headers обязателен: без него проверка IP в вебхуке ЮKassa
      // видит адрес nginx (127.0.0.1) и отклоняет все уведомления об оплате.
      // Бинд на 127.0.0.1 — наружу приложение смотрит только через nginx (HTTPS).
      args: "app.main:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips=127.0.0.1",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "500M",
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    },
    {
      name: "bot",
      // Legacy-бот: курс больше не продаёт, но даёт старым покупателям
      // кнопку «Войти на сайт». Требует BOT_ENABLED=true в .env.
      script: "/root/ai-course-platform/venv/bin/python",
      args: "-m app.bot.bot",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    },
    {
      name: "webinar-bot",
      // Лид-магнит + анонсы/напоминания вебинара.
      // Требует WEBINAR_BOT_ENABLED=true и WEBINAR_BOT_TOKEN в .env.
      // Использует тот же TELEGRAM_API_BASE (прокси), что и основной бот.
      script: "/root/ai-course-platform/venv/bin/python",
      args: "-m app.bot.webinar_bot",
      cwd: "/root/ai-course-platform",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      env: {
        PYTHONPATH: "/root/ai-course-platform"
      }
    }
  ]
}
