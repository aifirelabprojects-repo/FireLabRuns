import os
from datetime import datetime
from sqlalchemy import ( create_engine, Column, String, Integer, Text, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///chatbot.db")

# pool_pre_ping avoids stale connections in production
engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,            
    max_overflow=20,         
    future=True
)

Base = declarative_base()


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    status = Column(String, default="active")
    interest = Column(String(16), default="low")
    mood = Column(String(16), default="neutral")

    # questionnaire (consider separate table if evolving)
    q1_company = Column(Text)
    q1_email = Column(String(320))
    q1_email_domain = Column(String(255), index=True)
    q2_role = Column(Text)
    q3_categories = Column(Text)
    q4_services = Column(Text)
    q5_activity = Column(Text)
    q6_timeline = Column(Text)
    q7_budget = Column(Text)

    username = Column(String(120))
    mobile = Column(String(50))
    phase = Column(String(32), default="initial", nullable=False)
    routing = Column(String(32), default="none", nullable=False)

    verified = Column(Text)
    confidence = Column(Text)  
    evidence = Column(String)
    c_info = Column(Text)

    c_data = Column(String, nullable=True)
    c_sources = Column(String, nullable=True)
    c_images = Column(String, nullable=True)
    v_sources = Column(String, nullable=True)

    messages = relationship("Message",back_populates="session",cascade="all, delete-orphan",order_by="Message.timestamp",
    )

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    interest = Column(String)
    mood = Column(String)

    session = relationship("Session", back_populates="messages")


class CustomerBase(Base):
    __tablename__ = "customer"
    id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
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


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)
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

