# Инструкция по деплою на TimeWeb (PM2)

## Как устроен процесс

```
Локально (VS Code) → git push → GitHub → сервер: git pull + pm2 restart
```

---

## 0. Переход на веб-версию (выполняется один раз)

Обновление идёт поверх работающего прода с реальными покупателями. Порядок важен.

**Перед началом.** В `.env` на сервере должны быть (см. раздел 5):
`DEBUG=False`, `BOT_ENABLED=true`, `SITE_URL=https://davydovaai.ru` и заполненный блок `SMTP_*`.
Без SMTP не уйдут письма подтверждения и «установите пароль».

```bash
cd /root/ai-course-platform

# 1. Бэкап базы — там живут покупатели
sudo -u postgres pg_dump aicourse > ~/aicourse_before_web_$(date +%F).sql
ls -lh ~/aicourse_before_web_*.sql   # убедиться, что файл не пустой

# 2. Остановить старые процессы (они запущены ad-hoc, не из ecosystem.config.js)
pm2 delete all

# 3. Забрать код. ВНИМАНИЕ: venv/ убран из репозитория,
#    поэтому pull удалит каталог venv — так и задумано, ниже он пересоздаётся.
git pull origin main

# 4. Чистый venv под новые версии зависимостей
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# 5. Миграции (аддитивные: has_access и оплаты не трогают)
venv/bin/alembic upgrade head
venv/bin/alembic current      # ожидаем f7a8b9c0d1e2 (head)

# 6. Запуск из конфига репозитория (venv, 127.0.0.1, --proxy-headers)
pm2 start ecosystem.config.js
pm2 save
pm2 list                      # fastapi + bot online
```

**Проверка после запуска:**

```bash
curl -sI https://davydovaai.ru | head -1                      # 200/405 — сайт жив
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
     https://davydovaai.ru/webhooks/yookassa -d '{}'          # ожидаем 403
```

403 на вебхуке — это правильно: чужой IP отклонён. Если там 500 или 200 — значит
`--proxy-headers` не применился, и уведомления от ЮKassa обрабатываться не будут.

**Перевод покупателей на сайт:**

```bash
# Тем, у кого в базе есть email — письмо «мы переехали, установите пароль»
venv/bin/python -c "from app.tasks import notify_bot_migration; print(notify_bot_migration())"
```

У кого email нет — заходят в бот, жмут «🌐 Войти на сайт» (или команда `/site`)
и попадают в личный кабинет по одноразовой ссылке. Доступ к курсу сохраняется.

**Откат**, если что-то пошло не так:

```bash
pm2 delete all
git reset --hard 562a5ca                       # коммит старой версии
sudo -u postgres psql -c "DROP DATABASE aicourse;"
sudo -u postgres psql -c "CREATE DATABASE aicourse;"
sudo -u postgres psql aicourse < ~/aicourse_before_web_ДАТА.sql
python3 -m venv venv && venv/bin/pip install -r requirements.txt
pm2 start ecosystem.config.js && pm2 save
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

Файл `/root/ai-course-platform/.env` — создаётся вручную, НЕ через GitHub. Актуальный перечень полей см. в `.env.example` в репозитории. Минимально для веба + бота + почты:

```
APP_NAME=...
DEBUG=False
SECRET_KEY=ваш_секретный_ключ_минимум_32_символа
ADMIN_SECRET_CODE=...
DATABASE_URL=postgresql+asyncpg://postgres:ПАРОЛЬ@localhost:5432/aicourse
BOT_TOKEN=токен_бота
BOT_USERNAME=DavydovaAIBot
BOT_ENABLED=true
YOOKASSA_SHOP_ID=ваш_id
YOOKASSA_SECRET_KEY=ваш_ключ
KINESCOPE_API_KEY=ваш_ключ
KINESCOPE_PROJECT_ID=ваш_project_id
CLC_API_KEY=ваш_ключ
REDIS_URL=redis://localhost:6379/0
SITE_URL=https://davydovaai.ru
SMTP_HOST=...
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM=noreply@davydovaai.ru
```

После правок `.env` перезапустите процессы: `pm2 restart fastapi bot celery-worker celery-beat`.

Если бот не нужен на проде, задайте `BOT_ENABLED=false` и остановите процесс: `pm2 stop bot` (на сайте ссылка на Telegram всё равно останется для желающих общаться с ботом вручную).

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
