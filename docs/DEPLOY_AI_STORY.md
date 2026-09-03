# Deploy checklist — AI STORY update (Timeweb / Termius)

## Before deploy

1. Local: `alembic upgrade head`
2. Local: `pytest tests/ -q`
3. Set in local `.env` (and later on server):
   - `PRO_CHAT_INVITE_URL=https://t.me/+...` (Pro-канал)
   - `VIP_CHAT_INVITE_URL=https://t.me/+...` (VIP-канал)
   - `ADMIN_NOTIFY_EMAIL=you@example.com`
   - optional `ADMIN_TELEGRAM_ID=123456789`
4. Smoke: open `/`, check Pro/VIP cards, cabinet with test grant, payment create (sandbox).

## On server (Termius / SSH)

```bash
cd /path/to/ai-course-platform   # ваш каталог проекта на Timeweb
git pull origin main

# activate venv (имя может отличаться)
source .venv/bin/activate   # или: source venv/bin/activate

pip install -r requirements.txt

# добавить в .env новые переменные (см. выше)
nano .env

alembic upgrade head

# restart app — пример для systemd:
sudo systemctl restart ai-course    # подставьте своё имя сервиса
# если uvicorn вручную — остановите старый процесс и запустите снова:
# uvicorn app.main:app --host 0.0.0.0 --port 8000

# celery (если используется):
# sudo systemctl restart celery-worker celery-beat

# бот (только если BOT_ENABLED=true):
# sudo systemctl restart ai-course-bot
```

## After deploy

1. Admin → Курс → выберите «AI STORY» → проставьте реальные Kinescope `video_id` у уроков.
2. Проверьте тарифы Pro 9990 / VIP 29990.
3. Тестовая оплата или ручной grant пользователю.
4. Письмо после оплаты Pro/VIP содержит ссылку на соответствующий канал; VIP — также письмо админу на `ADMIN_NOTIFY_EMAIL`.
5. Бот: AI STORY первым, классический курс во втором пункте меню.

## Rollback

```bash
git checkout <previous-commit>
alembic downgrade -1   # снимает a1b2c3d4e5f6 — осторожно: удалит modules/tariffs/access
sudo systemctl restart ai-course
```
