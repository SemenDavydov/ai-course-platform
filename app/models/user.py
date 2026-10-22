import asyncio

from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger
from sqlalchemy.sql import func
from app.database import Base
import bcrypt


def _hash_password(password: str) -> str:
    password_bytes = password.encode('utf-8')[:72]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')


def _verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    password_bytes = password.encode('utf-8')[:72]
    return bcrypt.checkpw(password_bytes, password_hash.encode('utf-8'))


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=True, index=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, nullable=True)

    password_hash = Column(String, nullable=True)
    role = Column(String, default="user")
    is_blocked = Column(Boolean, default=False)
    accepted_offer = Column(Boolean, default=False)

    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    has_access = Column(Boolean, default=False)

    # Web auth fields
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verification_token = Column(String, nullable=True)
    email_verification_sent_at = Column(DateTime(timezone=True), nullable=True)
    name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    registration_source = Column(String, default="bot_migrated", nullable=False)
    password_reset_token = Column(String, nullable=True)
    password_reset_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Одноразовый вход на сайт из Telegram-бота (для покупателей без email)
    login_token = Column(String, nullable=True, unique=True, index=True)
    login_token_sent_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    access_granted_at = Column(DateTime(timezone=True), nullable=True)
    
    def set_password(self, password: str):
        """Устанавливает пароль (хэширует) с помощью bcrypt"""
        self.password_hash = _hash_password(password)

    def check_password(self, password: str) -> bool:
        """Проверяет пароль с помощью bcrypt"""
        return _verify_password(password, self.password_hash)

    # bcrypt занимает ~0.1–0.3 с CPU. В async-коде вызываем только эти версии:
    # синхронный вызов блокирует event loop, и поток логинов кладёт весь воркер.
    async def aset_password(self, password: str) -> None:
        self.password_hash = await asyncio.to_thread(_hash_password, password)

    async def acheck_password(self, password: str) -> bool:
        return await asyncio.to_thread(_verify_password, password, self.password_hash)

    def __repr__(self):
        return f"<User {self.telegram_id} {self.username}>"
