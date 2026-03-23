# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the FastAPI server
uvicorn app.main:app --reload

# Run the Telegram bot (standalone)
python app/bot/bot.py

# Run Celery worker
celery -A app.celery_app worker --loglevel=info

# Run Celery beat (scheduled tasks)
celery -A app.celery_app beat --loglevel=info

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"
alembic downgrade -1

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_payments.py -v
```

## Architecture

This is an AI course sales platform with three main runtime components:

1. **FastAPI web app** (`app/main.py`) - serves the admin panel (HTML/Jinja2), webhooks, and a REST API for the bot.
2. **Telegram bot** (`app/bot/bot.py`) - standalone aiogram bot that handles user interaction, payment initiation, and course access.
3. **Celery workers** (`app/celery_app.py`, `app/tasks.py`) - background tasks (email receipts, daily reports, DB backups). Uses Redis as broker.

All three share the same PostgreSQL database via SQLAlchemy async (`app/database.py`).

### Payment Flow

Bot collects user email -> `PaymentService.create_payment()` creates a YooKassa payment and stores a `pending` record -> user pays -> YooKassa calls `POST /webhooks/yookassa` -> webhook sets `payment.status = "succeeded"`, `user.has_access = True`, and sends a Telegram notification via the bot instance imported from `app/bot/bot.py`.

### Video Access Flow

Bot generates a JWT token (signed with `SECRET_KEY`, expires per `VIDEO_LINK_LIFETIME`) embedding `user_id` and `video_id` -> constructs URL `{SITE_URL}/api/v1/bot/video/{video_id}?token=...` -> optionally shortens via clc.li (`URLShortener`) -> user clicks link -> FastAPI validates JWT, fetches Kinescope embed URL, appends watermark param, renders `app/templates/video/player.html`.

### Key Design Decisions

- **`LESSON_DATA` dict** in `app/bot/bot.py` maps database lesson IDs to display labels/emojis. Lesson IDs are hardcoded here — if lesson IDs change in the DB, this dict must be updated.
- **Admin auth** uses cookie-based sessions stored in the `admin_sessions` table (not JWT). Session token is set in cookie `admin_session`; `get_current_admin` dependency validates it.
- **Two virtual environments**: `.venv/` (Python 3.13) and `venv/` (Python 3.12) both exist. The active one is `.venv/`.
- **User model** has both `is_admin: bool` and `role: str` ("user"/"admin"/"superadmin") — both must be kept in sync when changing roles.
- **`accepted_offer` flag** on `User` — users must accept the public offer before payment is allowed.

### Module Map

| Path | Purpose |
|------|---------|
| `app/config.py` | Pydantic settings, reads `.env` |
| `app/database.py` | Async SQLAlchemy engine, `get_db` dependency |
| `app/models/` | SQLAlchemy ORM models |
| `app/api/admin.py` | Admin panel routes (HTML, session auth) |
| `app/api/webhooks.py` | YooKassa payment webhook |
| `app/api/v1/bot_api.py` | REST API for bot + video player page |
| `app/api/v1/materials.py` | Material download endpoints |
| `app/bot/bot.py` | aiogram bot handlers |
| `app/services/payment.py` | YooKassa payment creation |
| `app/services/video.py` | Kinescope API + JWT link generation |
| `app/services/shortener.py` | clc.li URL shortening |
| `app/tasks.py` | Celery tasks |
| `alembic/versions/` | Migration history |
| `tests/conftest.py` | Test fixtures (separate `aicourse_test` DB) |

## Environment Variables

Copy `.env.example` to `.env`. Required: `SECRET_KEY`, `DATABASE_URL`, `BOT_TOKEN`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`. Optional but needed for full functionality: `KINESCOPE_API_KEY`, `KINESCOPE_PROJECT_ID`, `CLC_API_KEY`, `REDIS_URL`.

Test suite hardcodes `postgresql+asyncpg://postgres:password@localhost:5432/aicourse_test` in `tests/conftest.py`.
