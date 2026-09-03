from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean, Float, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Course(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    price = Column(Float, nullable=False)  # legacy fallback; tariffs hold sell prices
    is_published = Column(Boolean, default=False)
    slug = Column(String, unique=True, nullable=True, index=True)
    sort_order = Column(Integer, default=0, nullable=False)
    is_legacy = Column(Boolean, default=False, nullable=False)

    lessons = relationship("Lesson", back_populates="course", order_by="Lesson.order")
    modules = relationship(
        "Module", back_populates="course", order_by="Module.order", cascade="all, delete-orphan"
    )
    tariffs = relationship(
        "Tariff", back_populates="course", order_by="Tariff.price", cascade="all, delete-orphan"
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<Course {self.title}>"


class Module(Base):
    __tablename__ = "modules"

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    order = Column(Integer, nullable=False, default=1)
    button_label = Column(String, nullable=True)

    course = relationship("Course", back_populates="modules")
    lessons = relationship(
        "Lesson", back_populates="module", order_by="Lesson.order"
    )

    def __repr__(self):
        return f"<Module {self.order}: {self.title}>"


class Lesson(Base):
    __tablename__ = "lessons"

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    module_id = Column(Integer, ForeignKey("modules.id", ondelete="SET NULL"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    video_id = Column(String, nullable=True)  # Kinescope ID; "pending" until uploaded
    duration = Column(Integer, nullable=True)

    order = Column(Integer, nullable=False)

    course = relationship("Course", back_populates="lessons")
    module = relationship("Module", back_populates="lessons")
    materials = relationship("Material", backref="lesson", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Lesson {self.order}: {self.title}>"


class Tariff(Base):
    __tablename__ = "tariffs"

    id = Column(Integer, primary_key=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    slug = Column(String, nullable=False)  # pro | vip
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    old_price = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    features_markdown = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)

    course = relationship("Course", back_populates="tariffs")

    def __repr__(self):
        return f"<Tariff {self.slug} {self.price}>"


class UserCourseAccess(Base):
    __tablename__ = "user_course_access"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    tariff_slug = Column(String, nullable=False, default="legacy")  # legacy | pro | vip
    granted_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="course_accesses")
    course = relationship("Course")

    def __repr__(self):
        return f"<UserCourseAccess user={self.user_id} course={self.course_id} {self.tariff_slug}>"
