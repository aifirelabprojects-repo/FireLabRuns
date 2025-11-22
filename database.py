import os
from datetime import datetime
import uuid
from sqlalchemy.ext.asyncio import (create_async_engine, async_sessionmaker, AsyncAttrs)
from sqlalchemy import (Boolean, Column, String, Integer, Text, DateTime, ForeignKey)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///chatbot.db")  


engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,  #  for debugging
    future=True,  # for 2.0+ style
    pool_reset_on_return=None,
)

class Base(AsyncAttrs, DeclarativeBase):
    pass


class Session(Base):  
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    status: Mapped[str] = mapped_column(String, default="active")
    interest: Mapped[str] = mapped_column(String(16), default="low")
    mood: Mapped[str] = mapped_column(String(16), default="neutral")
    username: Mapped[str | None] = mapped_column(String(120))
    mobile: Mapped[str | None] = mapped_column(String(50))
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    phase_info: Mapped["SessionPhase"] = relationship(
        "SessionPhase", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    company_details: Mapped["CompanyDetails"] = relationship(
        "CompanyDetails", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    verification_details: Mapped["VerificationDetails"] = relationship(
        "VerificationDetails", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    research_details: Mapped["ResearchDetails"] = relationship(
        "ResearchDetails", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    consultations: Mapped[list["Consultation"]] = relationship(
        "Consultation", back_populates="session", cascade="all, delete-orphan"
    )
    
    project: Mapped["Project"] = relationship(
        "Project", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.timestamp"
    )

class SessionPhase(Base):  
    __tablename__ = "session_phase"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    q1_company: Mapped[str | None] = mapped_column(Text)
    q1_email: Mapped[str | None] = mapped_column(String(320))
    q1_email_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    q2_role: Mapped[str | None] = mapped_column(Text)
    q3_categories: Mapped[str | None] = mapped_column(Text)
    q4_services: Mapped[str | None] = mapped_column(Text)
    q5_activity: Mapped[str | None] = mapped_column(Text)
    q6_timeline: Mapped[str | None] = mapped_column(Text)
    q7_budget: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(String(32), default="initial", nullable=False)
    routing: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    session: Mapped["Session"] = relationship("Session", back_populates="phase_info")

class CompanyDetails(Base):  
    __tablename__ = "company_details"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # field names preserved exactly as requested
    c_info: Mapped[str | None] = mapped_column(Text)
    c_data: Mapped[str | None] = mapped_column(String, nullable=True)
    c_sources: Mapped[str | None] = mapped_column(String, nullable=True)
    c_images: Mapped[str | None] = mapped_column(String, nullable=True)
    session: Mapped["Session"] = relationship("Session", back_populates="company_details")

class VerificationDetails(Base):  
    __tablename__ = "verification_details"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # user/verification fields moved here (names unchanged)
    verified: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(String)
    v_sources: Mapped[str | None] = mapped_column(String, nullable=True)
    session: Mapped["Session"] = relationship("Session", back_populates="verification_details")

class ResearchDetails(Base):  
    __tablename__ = "research_details"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    research_data: Mapped[str | None] = mapped_column(String, nullable=True)
    research_sources: Mapped[str | None] = mapped_column(String, nullable=True)
    session: Mapped["Session"] = relationship("Session", back_populates="research_details")

class Message(Base):  
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    interest: Mapped[str | None] = mapped_column(String)
    mood: Mapped[str | None] = mapped_column(String)
    session: Mapped["Session"] = relationship("Session", back_populates="messages")

class CustomerBase(Base): 
    __tablename__ = "customer"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    groupcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[str | None] = mapped_column(Text, nullable=True)
    services: Mapped[str | None] = mapped_column(Text, nullable=True)
    activity: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeline: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    mobile: Mapped[str | None] = mapped_column(Text, nullable=True)

class Consultant(Base):
    __tablename__ = "consultants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tier: Mapped[str] = mapped_column(String(50), default="junior", nullable=False) # e.g., 'junior', 'senior', 'lead'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationship to Consultation
    consultations: Mapped[list["Consultation"]] = relationship(
        "Consultation", back_populates="consultant_info"
    )

class Consultation(Base):
    __tablename__ = "consultations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schedule_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="Pending", nullable=False, index=True
    )  # e.g., 'Pending', 'Confirmed', 'Completed'
    consultant_id: Mapped[str | None] = mapped_column(
        ForeignKey("consultants.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    session: Mapped["Session"] = relationship("Session", back_populates="consultations")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    consultant_info: Mapped["Consultant"] = relationship("Consultant", back_populates="consultations")

class ServiceTemplate(Base):
    __tablename__ = "service_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    default_tasks: Mapped[list["TemplateTask"]] = relationship("TemplateTask", back_populates="template", cascade="all, delete-orphan", order_by="TemplateTask.sequence_number")
    projects: Mapped[list["Project"]] = relationship("Project", back_populates="template")

class TemplateTask(Base):
    __tablename__ = "template_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("service_templates.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sequence_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_milestone: Mapped[bool] = mapped_column(Boolean, default=False)
    template: Mapped["ServiceTemplate"] = relationship("ServiceTemplate", back_populates="default_tasks")

class ProjectTask(Base):
    __tablename__ = "project_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    
    # Task Details
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    
    # Progress Tracking
    status: Mapped[str] = mapped_column(String(32), default="Pending", nullable=False) # e.g., 'Pending', 'In Progress', 'Completed', 'Canceled'
    sequence_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Relations
    project: Mapped["Project"] = relationship(
        "Project", back_populates="tasks"
    )
    files: Mapped[list["TaskFile"]] = relationship(
        "TaskFile", back_populates="task", cascade="all, delete-orphan", order_by="TaskFile.uploaded_at"
    )

class TaskFile(Base):
    __tablename__ = "task_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False
    )

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False) 
    mime_type: Mapped[str | None] = mapped_column(String(100))
    uploaded_by: Mapped[str | None] = mapped_column(String(100))

    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    task: Mapped["ProjectTask"] = relationship("ProjectTask", back_populates="files")

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True) # UUID or similar for easy sharing
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="Active", nullable=False) # e.g., 'Active', 'On Hold', 'Completed'
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    template_id: Mapped[int] = mapped_column(
        ForeignKey("service_templates.id"), nullable=True
    )
    template: Mapped["ServiceTemplate"] = relationship(
        "ServiceTemplate", back_populates="projects"
    )

    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    session: Mapped["Session"] = relationship(
        "Session", back_populates="project", uselist=False
    )
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tasks: Mapped[list["ProjectTask"]] = relationship(
        "ProjectTask", back_populates="project", cascade="all, delete-orphan", order_by="ProjectTask.sequence_number"
    )


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=AsyncSession,
    future=True,
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized successfully!")

async def get_db():
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()
        
