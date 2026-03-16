from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Данные платежа
    amount = Column(Float, nullable=False)
    payment_id = Column(String, unique=True, nullable=False)  # ID от ЮKassa
    status = Column(String, default="pending")  # pending, succeeded, cancelled
    description = Column(String, nullable=True)

    # Чеки для налоговой
    receipt_sent = Column(Boolean, default=False)
    receipt_data = Column(Text, nullable=True)  # JSON с данными чека

    # Временные метки
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)

    # Связи
    user = relationship("User")

    def __repr__(self):
        return f"<Payment {self.payment_id} {self.status}>"