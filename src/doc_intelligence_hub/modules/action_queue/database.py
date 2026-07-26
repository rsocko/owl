"""Database models and connection management."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Action(Base):
    """An action recommendation extracted from a Paperless document."""

    __tablename__ = "actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    document_title = Column(String, nullable=False)
    action_type = Column(String, nullable=False)  # PAY, RESPOND, FILE, etc.
    title = Column(String, nullable=False)
    summary = Column(Text)
    due_date = Column(Date, nullable=True)
    amount = Column(Float, nullable=True)
    urgency = Column(String, default="LOW")  # CRITICAL, HIGH, MEDIUM, LOW
    confidence = Column(Integer, default=0)
    risk_score = Column(Integer, default=0)  # 0-100 composite risk score
    status = Column(String, default="pending", index=True)  # pending, completed, dismissed
    last_synced_status = Column(String, nullable=True)  # What we last wrote to Paperless
    correspondent = Column(String, nullable=True)
    extracted_data = Column(JSON, nullable=True)  # Full extraction payload from Ollama
    ai_reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class ProcessingHistory(Base):
    """Tracks which documents have been processed to avoid duplicates."""

    __tablename__ = "processing_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, unique=True, index=True)
    document_checksum = Column(String, nullable=True)  # Detect content changes
    processed_at = Column(DateTime, default=datetime.utcnow)
    ollama_model = Column(String, nullable=True)
    success = Column(Integer, default=1)  # 1=success, 0=failed
    error_message = Column(Text, nullable=True)

    # Disposition: what happened when we processed this document
    # action_created | no_action_needed | unreadable | low_confidence
    disposition = Column(String, default="action_created")

    # Basic text quality metrics (free data for future OCR quality pipeline)
    content_length = Column(Integer, nullable=True)  # chars in OCR text
    word_count = Column(Integer, nullable=True)
    text_quality_score = Column(Integer, nullable=True)  # 0-100 heuristic


def get_engine():
    return create_engine(settings.database_url, echo=False)


def get_session() -> Session:
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def init_db():
    """Create all tables."""
    engine = get_engine()
    Base.metadata.create_all(engine)
