# Инструкция по деплою на TimeWeb

## 1. Первый деплой: настройка GitHub → Сервер

### На локальной машине (один раз)

```bash
# В терминале VS Code (в папке проекта)

# Убедись что репозиторий связан с GitHub
git remote -v
# Если пусто — добавь:
git remote add origin https://github.com/ВАШ_ЮЗЕРНЕЙМ/ai-course-platform.git

# Закоммить все изменения
git add app/ deploy/ alembic/
git commit -m "Add warm-up bot, landing page, deploy configs"
git push origin main
```

---

### На сервере (один раз — настройка)

Подключись через Termius, затем:

```bash
cd /root/ai-course-platform

# Убедись что remote настроен
git remote -v
# Если пусто:
git remote add origin https://github.com/ВАШ_ЮЗЕРНЕЙМ/ai-course-platform.git

# Получи код
git pull origin main
```

#### Установи systemd-сервисы

```bash
# Скопируй сервисные файлы
cp deploy/fastapi.service /etc/systemd/system/
cp deploy/bot.service /etc/systemd/system/
cp deploy/celery-worker.service /etc/systemd/system/
cp deploy/celery-beat.service /etc/systemd/system/

# Перезагрузи systemd
systemctl daemon-reload

# Включи автозапуск
systemctl enable fastapi bot celery-worker celery-beat

# Запусти сервисы
systemctl start fastapi bot celery-worker celery-beat

# Проверь статус
systemctl status fastapi
systemctl status bot
```

#### Настрой nginx

```bash
# Скопируй конфиг
cp deploy/nginx.conf /etc/nginx/sites-available/davydovaai.ru
ln -s /etc/nginx/sites-available/davydovaai.ru /etc/nginx/sites-enabled/

# Удали дефолтный конфиг если мешает
rm -f /etc/nginx/sites-enabled/default

# Проверь конфиг и перезапусти
nginx -t && systemctl reload nginx
```

#### Получи SSL-сертификат (Let's Encrypt)

```bash
apt install certbot python3-certbot-nginx -y
certbot --nginx -d davydovaai.ru -d www.davydovaai.ru

# После успеха раскомментируй HTTPS-блок в deploy/nginx.conf
# и снова: nginx -t && systemctl reload nginx
```

---

## 2. Обновление кода (каждый раз)

### Шаг 1 — На локальной машине

```bash
# Закоммить и отправить изменения
git add app/ deploy/
git commit -m "Описание изменений"
git push origin main
```

### Шаг 2 — На сервере (через Termius)

```bash
cd /root/ai-course-platform

# Получить изменения
git pull origin main

# Если были изменения в зависимостях (requirements.txt)
venv/bin/pip install -r requirements.txt

# Если были миграции БД
venv/bin/alembic upgrade head

# Перезапустить сервисы
systemctl restart fastapi bot

# Если менялись Celery-таски
systemctl restart celery-worker celery-beat
```

---

## 3. Просмотр логов

```bash
# FastAPI (последние 50 строк, в реальном времени)
journalctl -u fastapi -n 50 -f

# Бот
journalctl -u bot -n 50 -f

# Celery
journalctl -u celery-worker -n 50 -f

# Nginx
tail -f /var/log/nginx/error.log
```

---

## 4. Проверка статуса всех сервисов

```bash
systemctl status fastapi bot celery-worker celery-beat nginx postgresql
```

---

## 5. Важные замечания

- **`.env` НЕ попадает в GitHub** (защищён `.gitignore`) — на сервере он должен быть создан вручную
- **На сервере используй `venv/`** (Python 3.12), НЕ `.venv/`
- **`SITE_URL`** в `.env` на сервере должен быть `https://davydovaai.ru`
- Если сервисы уже запущены через `screen`/`tmux` — останови их перед запуском systemd-сервисов

### Шаблон .env для сервера

```
SECRET_KEY=ваш_секретный_ключ
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
