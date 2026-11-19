# План (актуализирован после сверки кода)

Дата сверки: 2026-04-27.  
Дата контрольной проверки: 2026-05-12.

## Пререлизный чеклист

- **Миграции:** на проде после деплоя `alembic upgrade head`.
- **`.env`:** `DEBUG=False`, `SITE_URL=https://…` (для cookies `Secure` и корректных ссылок в письмах), заполнены SMTP и YooKassa; при чисто веб-режиме `BOT_ENABLED=false`.
- **Cookie:** при `DEBUG=False` и `SITE_URL` с `https://` включаются `Secure` для `user_session`/`admin_session` и `https_only` у `SessionMiddleware`.
- **Тесты:** полный прогон `pytest tests/` из `.venv`; в `conftest` у `client` и `db_session` явная зависимость от `setup_database`, чтобы не было гонки схемы БД между асинхронными фикстурами.

## 1) Что реально уже сделано

### 1.1 Web-модель и инфраструктура
- Добавлены миграции для web-полей пользователя, сброса пароля и прогресса уроков:
  - `alembic/versions/a2b3c4d5e6f7_add_web_user_fields.py`
  - `alembic/versions/b3c4d5e6f7a8_add_password_reset_fields.py`
  - `alembic/versions/c4d5e6f7a8b9_add_lesson_progress.py`
- Есть модели `UserSession` и `LessonProgress`.
- Есть общий email-сервис `app/services/email.py` и Celery-обёртка `send_email_task` в `app/tasks.py`.

### 1.2 Auth и сессии
- Реализованы роуты в `app/api/auth.py`:
  - регистрация / логин / логаут,
  - verify-email,
  - resend-verification,
  - reset password,
  - setup password для мигрированных.
- Реализованы cookie-сессии пользователей через `user_sessions`.
- Подключение роутера и прокидывание текущего пользователя на лендинг сделаны в `app/main.py`.

### 1.3 Платежи и выдача доступа
- Есть `POST /api/v1/payments/create` (`app/api/v1/payments.py`).
- Есть страницы `GET /payment/success` и `GET /payment/failure` (`app/main.py`).
- Webhook `POST /webhooks/yookassa` (`app/api/webhooks.py`) выдает доступ и отправляет уведомление:
  - Telegram (если бот включен и пользователь ботовый),
  - иначе email.

### 1.4 ЛК / прогресс / профиль / админка
- Есть `app/api/cabinet.py`, `app/api/v1/progress.py`, шаблоны ЛК, профиль, прогресс.
- Добавлены тестовые файлы для auth/payment/cabinet/progress.

### 1.5 Выключаемость бота
- Флаг `BOT_ENABLED` есть в `app/config.py`.
- Guard на запуск бота добавлен в `app/bot/bot.py`.

## 2) Состояние после выравнивания кода и тестов

Раньше в репозитории были расхождения между планом, кодом и тестами (в частности вокруг email-верификации).
Сейчас они устранены:

- регистрация создаёт пользователя с `email_verified=False` и ставит письмо подтверждения в очередь,
- оплата/доступ к курсу не зависит от `email_verified`,
- чувствительные действия требуют подтверждённый email,
- тесты синхронизированы и проходят.

Примечание по окружению: тесты корректно запускаются из `.venv` (а не из системного `python`).

## 3) Принятое решение по верификации

- **SMS-верификацию не делаем.**
- **Доступ к курсу открывается сразу после успешной оплаты** (факт платежа = верификация для доступа).
- **Email-верификация нужна только для “чувствительных” действий**:
  - удаление аккаунта,
  - (в будущем) смена email.

## 4) Что уже внедрено по этой схеме

### 4.1 Регистрация и письмо подтверждения
- При регистрации `email_verified=False`.
- Генерируется `email_verification_token`, ставится `email_verification_sent_at`.
- Письмо подтверждения ставится в очередь через `send_email_task.delay(...)`.

### 4.2 Оплата и доступ к курсу
- `POST /api/v1/payments/create` **не требует** `email_verified=True`.
- Доступ выдаётся в webhook `POST /webhooks/yookassa` (`user.has_access=True`).

### 4.3 Чувствительные действия
- `POST /auth/delete-account` теперь требует `email_verified=True`,
  иначе редирект на `/cabinet/profile?error=email_not_verified`.
- В профиле добавлена кнопка “Отправить письмо для подтверждения”.

### 4.4 Тесты
- Тесты приведены к выбранной логике и прогнаны через `.venv`:
  - `tests/test_auth.py` — OK
  - `tests/test_web_payment.py` — OK
  - `tests/test_cabinet.py` + `tests/test_progress.py` — OK

## 4.5 Проверка безопасности сканерами (2026-11-19)

Инструменты заведены по просьбе пользователя. Команды — в `CLAUDE.md`.

### gitleaks 8.30.1 (локальный бинарник, `%LOCALAPPDATA%\gitleaks`)
- **История git: 0 находок** на 62 коммитах. `.env` в репозиторий никогда не попадал.
- **Найдено и исправлено:** в `tests/conftest.py` был зашит пароль БД, совпадающий
  с продовым, а файл лежит в git. Теперь URL берётся из `TEST_DATABASE_URL`.
  Смягчало риск то, что Postgres слушает только `127.0.0.1`, а репозиторий приватный.
- **Найдено и устранено на сервере:** в `/root/.pm2/logs` лежали 4 осиротевших лога
  (55 МБ) от старых имён процессов `ai-course-api` / `ai-course-bot`. В
  `ai-course-bot-error.log` был **действующий** BOT_TOKEN: `pm2 flush` такие файлы
  не чистит, поэтому прошлая зачистка их пропустила. Логи удалены, осталось 3.2 МБ,
  токенов в логах 0. **Ротация токена не требуется** — файлы наружу не отдавались.
- Ложные срабатывания (`.venv`, тестовые хеши passlib, константы sqlalchemy)
  закрыты через `.gitleaks.toml`.
- SMTP-пароль **не утекал**: строки `smtp_password` в логах — это запросы
  сканера-бота (185.177.72.69) к путям вида `/api/smtp_password.txt`, все 404.

### OWASP ZAP (baseline, docker `ghcr.io/zaproxy/zaproxy:stable`)
- Сканируется **только локальная копия** на `127.0.0.1:8099` с тестовой БД.
  **Прод сканировать нельзя:** активный скан наделает заявок и платежей у живых
  покупателей, а на VPS 1.9 ГБ RAM и нет docker — ZAP там не поднять.
- Результат: **0 FAIL, 58 PASS, 10 WARN**. SQL-инъекций и XSS не найдено.
- Закрыты два заголовка: `Strict-Transport-Security` (только при `cookie_secure`)
  и `Permissions-Policy`.
- «Sensitive Information in URL» на `/auth/open-lessons` — **ложная тревога**:
  пароль в query подставил сам спайдер ZAP. GET принимает только `token`/`error`,
  вход идёт через POST с токеном в теле формы.
- **Не сделано (отдельный шаг):** CSP. Строгая политика сломает плеер Kinescope,
  виджет ЮKassa и inline-скрипты лендинга — нужен прогон в `report-only`.

## 5) Следующий небольшой шаг (если захотим “смену email”)

- Добавить flow смены email через подтверждение нового адреса (token на **новый** email),
  и требовать `email_verified=True` для старого email перед началом смены.
