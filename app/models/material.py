from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, DateTime
from sqlalchemy.sql import func
from app.database import Base


class Material(Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True)
    lesson_id = Column(Integer, ForeignKey("lessons.id"), nullable=False)
    title = Column(String, nullable=False)  # Название файла для отображения
    file_name = Column(String, nullable=False)  # Имя файла на сервере
    original_name = Column(String, nullable=False)  # Оригинальное имя файла
    file_size = Column(Integer, nullable=False)  # Размер в байтах
    file_type = Column(String, nullable=False)  # mime-type
    downloads_count = Column(Integer, default=0)  # Счетчик скачиваний
    created_at = Column(DateTime(timezone=True), server_default=func.now())