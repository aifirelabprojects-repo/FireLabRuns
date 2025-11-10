import os
from datetime import datetime
from sqlalchemy.ext.asyncio import (create_async_engine, async_sessionmaker, AsyncAttrs)
from sqlalchemy import (Boolean, Column, String, Integer, Text, DateTime, ForeignKey)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///chatbot.db")  # Use aiosqlite for SQLite async

# For async, use create_async_engine
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,  #  for debugging
    future=True,  # For 2.0+ style
    pool_reset_on_return=None,
)

class Base(AsyncAttrs, DeclarativeBase):
    pass

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    status: Mapped[str] = mapped_column(String, default="active")
    interest: Mapped[str] = mapped_column(String(16), default="low")
    mood: Mapped[str] = mapped_column(String(16), default="neutral")

    # questionnaire (consider separate table if evolving)
    q1_company: Mapped[str | None] = mapped_column(Text)
    q1_email: Mapped[str | None] = mapped_column(String(320))
    q1_email_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    q2_role: Mapped[str | None] = mapped_column(Text)
    q3_categories: Mapped[str | None] = mapped_column(Text)
    q4_services: Mapped[str | None] = mapped_column(Text)
    q5_activity: Mapped[str | None] = mapped_column(Text)
    q6_timeline: Mapped[str | None] = mapped_column(Text)
    q7_budget: Mapped[str | None] = mapped_column(Text)

    username: Mapped[str | None] = mapped_column(String(120))
    mobile: Mapped[str | None] = mapped_column(String(50))
    phase: Mapped[str] = mapped_column(String(32), default="initial", nullable=False)
    routing: Mapped[str] = mapped_column(String(32), default="none", nullable=False)

    verified: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(String)
    c_info: Mapped[str | None] = mapped_column(Text)

    c_data: Mapped[str | None] = mapped_column(String, nullable=True)
    c_sources: Mapped[str | None] = mapped_column(String, nullable=True)
    c_images: Mapped[str | None] = mapped_column(String, nullable=True)
    v_sources: Mapped[str | None] = mapped_column(String, nullable=True)
    research_data: Mapped[str | None] = mapped_column(String, nullable=True)

    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.timestamp"
    )

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


# Async sessionmaker
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
        
