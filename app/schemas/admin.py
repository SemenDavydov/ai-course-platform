from pydantic import BaseModel, EmailStr
from typing import Optional

class AdminLogin(BaseModel):
    username: str
    password: str

class AdminRegister(BaseModel):
    username: str
    email: EmailStr
    password: str
    admin_code: str  # Секретный код для регистрации админа

class UserUpdate(BaseModel):
    is_blocked: Optional[bool] = None
    has_access: Optional[bool] = None
    role: Optional[str] = None

class CourseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    is_published: Optional[bool] = None

class LessonCreate(BaseModel):
    title: str
    description: str
    video_id: Optional[str] = None
    order: int

class LessonUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    video_id: Optional[str] = None
    order: Optional[int] = None