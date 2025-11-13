from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from sqlalchemy.types import JSON


# Ensure data directory exists for sqlite file
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / 'medical_scribe.db'
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

# For SQLite + FastAPI, allow check_same_thread=False
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Doctor(Base):
    __tablename__ = 'doctors'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    department = Column(String(255), nullable=True)
    designation = Column(String(255), nullable=True)
    patient_type = Column(String(64), nullable=True)  # Out-Patient / In-Patient
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('name', 'department', 'designation', 'patient_type', name='uq_doctor_identity'),
    )

    consultations = relationship('Consultation', back_populates='doctor')


class Patient(Base):
    __tablename__ = 'patients'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    uhid = Column(String(128), nullable=False, unique=True, index=True)
    sex = Column(String(32), nullable=True)
    age = Column(String(8), nullable=True)  # store as string to avoid parsing issues
    dob = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    consultations = relationship('Consultation', back_populates='patient')


class Consultation(Base):
    __tablename__ = 'consultations'
    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey('doctors.id'), nullable=False)
    patient_id = Column(Integer, ForeignKey('patients.id'), nullable=False)

    transcript_text = Column(Text, nullable=True)
    summary_text = Column(Text, nullable=True)
    symptoms_present = Column(JSON, nullable=True)
    unknown_mentions = Column(JSON, nullable=True)
    audio_path = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    doctor = relationship('Doctor', back_populates='consultations')
    patient = relationship('Patient', back_populates='consultations')


# Create tables on import
Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
