"""Site-wide key/value settings and waitlist (pre-enrollment) applications."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func

from app.database import Base


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)


class WaitlistApplication(Base):
    __tablename__ = "waitlist_applications"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(64), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    social = Column(String(255), nullable=True)
    accepted_privacy = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
