# Инструкция по деплою на TimeWeb (PM2)

## Как устроен процесс

```
Локально (VS Code) → git push → GitHub → сервер: git pull + pm2 restart
```

---

## 1. Первый деплой: настройка GitHub → Сервер

### На локальной машине (один раз)

```bash
# Убедись что репозиторий связан с GitHub
git remote -v

# Если пусто — добавь:
git remote add origin https://github.com/ВАШ_ЮЗЕРНЕЙМ/ai-course-platform.git

# Первый коммит и пуш
git add app/ deploy/ ecosystem.config.js alembic/
git commit -m "Add warm-up bot, landing page, deploy configs"
git push origin main
```

---

### На сервере (один раз — первичная настройка)

Подключись через Termius:

```bash
cd /root/ai-course-platform

# Получи свежий код
git pull origin main

# Если PM2 уже запущен со старыми процессами — перезагрузи с новым конфигом
pm2 delete all
pm2 start ecosystem.config.js
pm2 save  # сохранить список процессов для автозапуска при ребуте
pm2 startup  # скопируй и выполни команду которую он выдаст
```

---

## 2. Обновление кода (каждый раз)

### Шаг 1 — Локально

```bash
git add app/
git commit -m "Описание изменений"
git push origin main
```

### Шаг 2 — На сервере (Termius)

```bash
cd /root/ai-course-platform

# Получить изменения
git pull origin main

# Если менялись зависимости (requirements.txt)
venv/bin/pip install -r requirements.txt

# Если были новые миграции БД
venv/bin/alembic upgrade head

# Перезапустить сервисы
pm2 restart fastapi bot

# Если менялись Celery-таски — перезапустить и их
pm2 restart celery-worker celery-beat
```

---

## 3. Полезные PM2 команды

```bash
# Статус всех процессов
pm2 status

# Логи в реальном времени
pm2 logs           # все сразу
pm2 logs fastapi   # только FastAPI
pm2 logs bot       # только бот

# Перезапуск
pm2 restart fastapi
pm2 restart all

# Остановить / удалить
pm2 stop fastapi
pm2 delete fastapi
```

---

## 4. Nginx и SSL

```bash
# Скопируй конфиг (один раз)
cp /root/ai-course-platform/deploy/nginx.conf /etc/nginx/sites-available/davydovaai.ru
ln -s /etc/nginx/sites-available/davydovaai.ru /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Проверь и примени
nginx -t && systemctl reload nginx

# Получи SSL (один раз)
apt install certbot python3-certbot-nginx -y
certbot --nginx -d davydovaai.ru -d www.davydovaai.ru
```

После certbot раскомментируй HTTPS-блок в `deploy/nginx.conf` и снова:
```bash
nginx -t && systemctl reload nginx
```

---

## 5. Шаблон .env на сервере

Файл `/root/ai-course-platform/.env` — создаётся вручную, НЕ через GitHub:

```
SECRET_KEY=ваш_секретный_ключ_минимум_32_символа
DATABASE_URL=postgresql+asyncpg://postgres:ПАРОЛЬ@localhost:5432/aicourse
BOT_TOKEN=токен_бота
BOT_USERNAME=DavydovaAIBot
YOOKASSA_SHOP_ID=ваш_id
YOOKASSA_SECRET_KEY=ваш_ключ
KINESCOPE_API_KEY=ваш_ключ
KINESCOPE_PROJECT_ID=ваш_project_id
CLC_API_KEY=ваш_ключ
REDIS_URL=redis://localhost:6379/0
SITE_URL=https://davydovaai.ru
```

---

## 6. Проверка что всё работает

```bash
pm2 status
# Все 4 процесса должны быть online: fastapi, bot, celery-worker, celery-beat

curl http://127.0.0.1:8000
# Должен вернуть HTML лендинга

systemctl status nginx
# Active: active (running)
```
