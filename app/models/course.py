from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean, Float, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Course(Base):
    __tablename__ = 'courses'

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    price = Column(Float, nullable=False)
    is_published = Column(Boolean, default=False)

    # Связи
    lessons = relationship("Lesson", back_populates="course", order_by="Lesson.order")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<Course {self.title}>"


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    # Видео
    video_id = Column(String, nullable=False) # ID видео в Kinescope
    duration = Column(Integer, nullable=True) # Длительность в секундах

    # Порядок в курсе
    order = Column(Integer, nullable=False)

    # Связи
    course = relationship("Course", back_populates="lessons")
    materials = relationship("Material", backref="lesson", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Lesson {self.order}: {self.title}>"