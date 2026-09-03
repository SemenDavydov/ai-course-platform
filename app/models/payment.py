from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    amount = Column(Float, nullable=False)
    payment_id = Column(String, unique=True, nullable=False)
    status = Column(String, default="pending")  # pending, succeeded, cancelled
    description = Column(String, nullable=True)

    course_id = Column(Integer, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True)
    tariff_id = Column(Integer, ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True)
    tariff_slug = Column(String, nullable=True)  # pro | vip | legacy

    receipt_sent = Column(Boolean, default=False)
    receipt_data = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")
    course = relationship("Course")
    tariff = relationship("Tariff")

    def __repr__(self):
        return f"<Payment {self.payment_id} {self.status}>"
