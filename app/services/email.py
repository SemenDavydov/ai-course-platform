import ssl
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 15  # секунд


def _build_ssl_context() -> ssl.SSLContext:
    """Создаёт SSL-контекст совместимый с Python 3.12 и Timeweb."""
    ctx = ssl.create_default_context()
    # Timeweb использует самоподписанный или промежуточный сертификат —
    # отключаем проверку имени хоста и сертификата для надёжности
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def send_email(to: str, subject: str, html_body: str) -> bool:
    """
    Отправляет HTML-письмо через SMTP.
    Возвращает True при успехе, False при ошибке.
    """
    if not settings.SMTP_HOST or not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP not configured, skipping email to %s", to)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if settings.SMTP_PORT == 465:
            # Implicit SSL (SMTPS)
            ctx = _build_ssl_context()
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT,
                context=ctx, timeout=SMTP_TIMEOUT
            ) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # STARTTLS (порт 587 или 25)
            with smtplib.SMTP(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT
            ) as server:
                server.ehlo()
                server.starttls(context=_build_ssl_context())
                server.ehlo()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)

        logger.info("Email sent to %s: %s", to, subject)
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error("SMTP auth failed for %s: %s", to, e)
        return False
    except smtplib.SMTPException as e:
        logger.error("SMTP error sending to %s: %s", to, e)
        return False
    except OSError as e:
        logger.error("Network error sending email to %s: %s", to, e)
        return False
