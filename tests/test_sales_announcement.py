"""Рассылка «Стартовали продажи» по предзаписи."""
import pytest

from app.models.site import WaitlistApplication
from app.scripts import send_sales_announcement as mailing


def test_render_contains_key_info(monkeypatch):
    monkeypatch.setattr(mailing.settings, "SITE_URL", "https://davydovaai.ru")
    html, text = mailing._render("29 октября")
    for part in ("потоке 2.0", "до 29 октября", "«Самостоятельный»", "8 мест", "3 места",
                 "рассрочку на 6 месяцев", "@kolobesyatv", "@Simba1328",
                 'href="https://davydovaai.ru/"', "оставили заявку на предзапись"):
        assert part in html, part
    assert "до 29 октября" in text and "https://davydovaai.ru/" in text


@pytest.mark.asyncio
async def test_recipients_dedup_exclude_and_validate(db_session, monkeypatch):
    from tests.conftest import async_session_maker

    monkeypatch.setattr(mailing, "AsyncSessionLocal", async_session_maker)
    for email in ("A@Example.com", " a@example.com ", "lrozinovaa@gmail.com",
                  "jtkvdtei@formtests.info", "bad-email", "b@example.com"):
        db_session.add(WaitlistApplication(name="n", phone="1", email=email, accepted_privacy=True))
    await db_session.commit()

    got = await mailing._recipients({"lrozinovaa@gmail.com", "jtkvdtei@formtests.info"})
    assert got == ["a@example.com", "b@example.com"]


def test_journal_prevents_duplicates(tmp_path):
    journal = mailing._journal(tmp_path)
    journal.write_text("x@example.com\nY@Example.com\n", encoding="utf-8")
    assert mailing._already_sent(journal) == {"x@example.com", "y@example.com"}


def test_send_email_attaches_text_part(monkeypatch):
    from app.services import email as email_mod

    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, context=None): pass
        def login(self, u, p): pass
        def send_message(self, msg): sent["msg"] = msg

    monkeypatch.setattr(email_mod.settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(email_mod.settings, "SMTP_PORT", 2525)
    monkeypatch.setattr(email_mod.settings, "SMTP_USER", "u")
    monkeypatch.setattr(email_mod.settings, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)
    assert email_mod.send_email("to@example.com", "Тема", "<b>html</b>", "text")
    types = [part.get_content_type() for part in sent["msg"].get_payload()]
    assert types == ["text/plain", "text/html"]
