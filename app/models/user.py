from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger
from sqlalchemy.sql import func
from app.database import Base
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=True, index=True)
    username = Column(String(50), nullable=True)
    first_name = Column(String(50), nullable=True)
    last_name = Column(String(50), nullable=True)
    email = Column(String, nullable=True)
    accepted_offer = Column(Boolean, default=False)
    password_hash = Column(String, nullable=True)  # Для админов
    role = Column(String, default="user")  # roles: user, admin, superadmin
    is_blocked = Column(Boolean, default=False)  # Блокировка пользователя

    # Статусы
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    has_access = Column(Boolean, default=False)  # Купил ли курс

    # Временные метки
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    access_granted_at = Column(DateTime(timezone=True), nullable=True)

    def set_password(self, password: str):
        password_bytes = password.encode('utf-8')[:72]
        self.password_hash = pwd_context.hash(password_bytes)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        password_bytes = password.encode('utf-8')[:72]
        return pwd_context.verify(password_bytes, self.password_hash)

    def __repr__(self):
        return f"<User {self.telegram_id} {self.username}>"
