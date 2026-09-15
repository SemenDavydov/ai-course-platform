"""Processed inbound webhook events (idempotency)."""
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint, Text
from sqlalchemy.sql import func

from app.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_event"),
    )

    id = Column(Integer, primary_key=True)
    provider = Column(String, nullable=False)  # proonline | yookassa
    event_id = Column(String, nullable=False)
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<WebhookEvent {self.provider}:{self.event_id}>"
