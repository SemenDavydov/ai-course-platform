from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger
from sqlalchemy.sql import func
from app.database import Base
import bcrypt

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
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    access_granted_at = Column(DateTime(timezone=True), nullable=True)
    
    def set_password(self, password: str):
        """Устанавливает пароль (хэширует) с помощью bcrypt"""
        password_bytes = password.encode('utf-8')[:72]
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password_bytes, salt).decode('utf-8')
    
    def check_password(self, password: str) -> bool:
        """Проверяет пароль с помощью bcrypt"""
        if not self.password_hash:
            return False
        password_bytes = password.encode('utf-8')[:72]
        return bcrypt.checkpw(password_bytes, self.password_hash.encode('utf-8'))
    
    def __repr__(self):
        return f"<User {self.telegram_id} {self.username}>"
