from datetime import datetime
from sqlalchemy import (
    create_engine, Column, String, Integer, Text, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import os
# Use environment variable for the database URL (secure & flexible)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///chatbot.db")

# echo=True prints all SQL statements (for debugging)
# pool_pre_ping avoids stale connections in production
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,            # number of connections to keep open
    max_overflow=20,         # extra connections allowed temporarily
    future=True
)

Base = declarative_base()


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String, default="active")
    interest = Column(String, default="low")
    mood = Column(String, default="neutral")
    q1_company = Column(Text, nullable=True)
    q1_email = Column(Text, nullable=True)
    q1_email_domain = Column(Text, nullable=True)
    q2_role = Column(Text, nullable=True)
    q3_categories = Column(Text, nullable=True)
    q4_services = Column(Text, nullable=True)
    q5_activity = Column(Text, nullable=True)
    q6_timeline = Column(Text, nullable=True)
    q7_budget = Column(Text, nullable=True)
    username = Column(Text, nullable=True)
    mobile = Column(Text, nullable=True)
    phase = Column(String, default="initial")
    routing = Column(String, default="none")

    verified = Column(String, nullable=True)
    confidence = Column(String, nullable=True)
    evidence = Column(Text, nullable=True)
    sources = Column(Text, default="[]")

    # Relationship to messages (one-to-many)
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    interest = Column(String)
    mood = Column(String)

    session = relationship("Session", back_populates="messages")


class CustomerBase(Base):
    __tablename__ = "customer"
    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    company = Column(Text, nullable=True)
    groupcode = Column(Text, nullable=True)
    email = Column(Text, nullable=True)
    role = Column(Text, nullable=True)
    categories = Column(Text, nullable=True)
    services = Column(Text, nullable=True)
    activity = Column(Text, nullable=True)
    timeline = Column(Text, nullable=True)
    budget = Column(Text, nullable=True)
    username = Column(Text, nullable=True)
    mobile = Column(Text, nullable=True)

# Create a session factory (thread-safe)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def init_db():
    """Initialize database tables (create if not exist)."""
    Base.metadata.create_all(bind=engine)
    print("Database initialized successfully!")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

