"""
Рассылка «Стартовали продажи» по предзаписи (waitlist_applications).

    # посмотреть получателей (ничего не отправляет)
    venv/bin/python -m app.scripts.send_sales_announcement --exclude a@x.ru --exclude b@y.ru
    # тест на один адрес (адрес попадает в журнал — второй раз ему не уйдёт)
    venv/bin/python -m app.scripts.send_sales_announcement --test-to me@x.ru
    # боевая отправка
    venv/bin/python -m app.scripts.send_sales_announcement --exclude ... --send

Повторный запуск безопасен: отправленные адреса пишутся в журнал
var/mailings/<campaign>.sent и пропускаются.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.site import WaitlistApplication
from app.services.email import send_email
from app.services.site_settings import MSK, _MONTHS_GEN, get_sales_schedule

logger = logging.getLogger("sales_announcement")

CAMPAIGN = "sales-open-2026-10"
SUBJECT = "Стартовали продажи AI STORY — успей занять место в потоке 2.0"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PAUSE_SECONDS = 3  # не шлём пачкой — почтовики так реже режут в спам


def _render(sales_end_text: str) -> tuple[str, str]:
    site_url = (settings.SITE_URL or "").rstrip("/")
    site_host = urlparse(site_url).netloc or site_url
    env = Environment(loader=FileSystemLoader("app/templates/emails"), autoescape=select_autoescape(["html"]))
    html = env.get_template("sales_open.html").render(
        site_url=site_url, site_host=site_host, sales_end_text=sales_end_text
    )
    text = (
        "Успей занять своё место в потоке 2.0!\n"
        "Стартовали продажи моего обучения AI STORY.\n\n"
        f"Тарифы, программа и покупка: {site_url}/\n\n"
        f"Окно продаж — до {sales_end_text}!\n\n"
        "Места ограничены по тарифам:\n"
        "Тариф «Самостоятельный» — безлимитный\n"
        "Тариф «PRO» — 8 мест\n"
        "Тариф «VIP» — 3 места\n\n"
        "Тарифы PRO и VIP можно выгодно оформить в рассрочку на 6 месяцев.\n\n"
        "Буду рада видеть тебя в числе моих учеников!\n"
        "После оплаты выбранного тарифа тебе придёт ссылка на ТГ-чат — дождись её!\n\n"
        "Вопросы — мне в ТГ: @kolobesyatv или менеджеру: @Simba1328\n\n"
        f"Вы получили это письмо, потому что оставили заявку на предзапись на сайте {site_host}."
    )
    return html, text


def _journal(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{CAMPAIGN}.sent"


def _already_sent(journal: Path) -> set[str]:
    if not journal.exists():
        return set()
    return {line.strip().lower() for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()}


async def _recipients(exclude: set[str]) -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(func.lower(func.trim(WaitlistApplication.email))).distinct())
        emails = sorted({e for (e,) in rows if e})
    return [e for e in emails if e not in exclude and EMAIL_RE.match(e)]


async def _sales_end_text() -> str:
    async with AsyncSessionLocal() as db:
        schedule = await get_sales_schedule(db)
    if not schedule.end:
        raise SystemExit("Расписание продаж не задано — нечего писать в «Окно продаж до …»")
    end = schedule.end.astimezone(MSK)
    return f"{end.day} {_MONTHS_GEN[end.month - 1]}"


async def _load(exclude: set[str], need_recipients: bool) -> tuple[str, list[str]]:
    """Всё чтение из БД — в одном event loop: пул asyncpg привязан к циклу,
    второй asyncio.run на тех же соединениях падает «attached to a different loop»."""
    try:
        sales_end_text = await _sales_end_text()
        recipients = await _recipients(exclude) if need_recipients else []
    finally:
        # Закрываем пул в этом же цикле: соединения не переживут его закрытие.
        engine = AsyncSessionLocal.kw.get("bind")
        if engine is not None:
            await engine.dispose()
    return sales_end_text, recipients


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exclude", action="append", default=[], help="email, которому не отправлять")
    parser.add_argument("--test-to", help="отправить одно тестовое письмо на этот адрес")
    parser.add_argument("--send", action="store_true", help="боевая отправка по списку")
    parser.add_argument("--log-dir", default="var/mailings")
    args = parser.parse_args(argv)

    exclude = {e.strip().lower() for e in args.exclude}
    journal = _journal(Path(args.log_dir))
    sent = _already_sent(journal)
    sales_end_text, recipients = asyncio.run(_load(exclude, need_recipients=not args.test_to))
    html, text = _render(sales_end_text)

    if args.test_to:
        targets = [args.test_to.strip().lower()]
    else:
        targets = [e for e in recipients if e not in sent]

    print(f"Кампания: {CAMPAIGN} | окно продаж до: {sales_end_text}")
    print(f"Исключены: {sorted(exclude) or '—'} | уже отправлено ранее: {len(sent)}")
    print(f"К отправке ({len(targets)}):")
    for e in targets:
        print("  -", e)

    if not (args.send or args.test_to):
        print("\nПробный прогон: ничего не отправлено. Для отправки добавьте --send.")
        return

    ok = failed = 0
    with journal.open("a", encoding="utf-8") as log:
        for i, email in enumerate(targets):
            if send_email(email, SUBJECT, html, text):
                log.write(email + "\n")
                log.flush()
                ok += 1
                print(f"  ✓ {email}")
            else:
                failed += 1
                print(f"  ✗ {email} — ошибка SMTP, повторный запуск попробует снова")
            if i < len(targets) - 1:
                time.sleep(PAUSE_SECONDS)
    print(f"\nГотово: отправлено {ok}, ошибок {failed}. Журнал: {journal}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
