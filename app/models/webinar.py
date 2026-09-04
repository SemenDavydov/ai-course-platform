"""Webinar / lead-magnet Telegram bot subscribers and broadcast log."""
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Boolean, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class WebinarSubscriber(Base):
    __tablename__ = "webinar_subscribers"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    lead_magnet_sent = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WebinarBroadcastLog(Base):
    """Идемпотентность рассылок: один campaign_key — одна успешная рассылка."""
    __tablename__ = "webinar_broadcast_log"
    __table_args__ = (UniqueConstraint("campaign_key", name="uq_webinar_broadcast_campaign"),)

    id = Column(Integer, primary_key=True)
    campaign_key = Column(String(64), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    recipients_total = Column(Integer, default=0)
    recipients_ok = Column(Integer, default=0)
    recipients_fail = Column(Integer, default=0)
    note = Column(Text, nullable=True)
