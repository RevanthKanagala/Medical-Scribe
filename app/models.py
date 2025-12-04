"""Database models for Medical Scribe application using SQLAlchemy."""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class Doctor(Base):
    """Doctor information table."""
    __tablename__ = 'doctors'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    department = Column(String(100), nullable=False)
    designation = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    consultations = relationship("Consultation", back_populates="doctor")
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'department': self.department,
            'designation': self.designation,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Patient(Base):
    """Patient information table."""
    __tablename__ = 'patients'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    uhid = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    sex = Column(String(10), nullable=False)
    age = Column(Integer, nullable=False)
    dob = Column(String(20), nullable=False)  # Date of birth
    phone = Column(String(20), nullable=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    consultations = relationship("Consultation", back_populates="patient")
    
    def to_dict(self):
        return {
            'id': self.id,
            'uhid': self.uhid,
            'name': self.name,
            'sex': self.sex,
            'age': self.age,
            'dob': self.dob,
            'phone': self.phone,
            'email': self.email,
            'address': self.address,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Consultation(Base):
    """Consultation/Visit table - links doctor, patient, and medical data."""
    __tablename__ = 'consultations'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_id = Column(Integer, ForeignKey('doctors.id'), nullable=False)
    patient_id = Column(Integer, ForeignKey('patients.id'), nullable=False)
    patient_type = Column(String(20), nullable=False)  # Out-Patient / In-Patient
    visit_datetime = Column(DateTime, nullable=False)
    
    # Audio and transcript
    audio_file_path = Column(String(500), nullable=True)
    transcript = Column(Text, nullable=False)
    transcript_length = Column(Integer, nullable=True)
    
    # Symptoms extracted (JSON stored as text)
    symptoms_json = Column(Text, nullable=True)  # Validated symptoms from AIMS
    symptom_count = Column(Integer, default=0)
    unknown_symptoms_json = Column(Text, nullable=True)
    
    # Medical summary
    summary = Column(Text, nullable=False)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    doctor = relationship("Doctor", back_populates="consultations")
    patient = relationship("Patient", back_populates="consultations")
    
    def to_dict(self):
        import json
        return {
            'id': self.id,
            'doctor_id': self.doctor_id,
            'patient_id': self.patient_id,
            'patient_type': self.patient_type,
            'visit_datetime': self.visit_datetime.isoformat() if self.visit_datetime else None,
            'audio_file_path': self.audio_file_path,
            'transcript': self.transcript,
            'transcript_length': self.transcript_length,
            'symptoms': json.loads(self.symptoms_json) if self.symptoms_json else [],
            'symptom_count': self.symptom_count,
            'unknown_symptoms': json.loads(self.unknown_symptoms_json) if self.unknown_symptoms_json else [],
            'summary': self.summary,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'doctor': self.doctor.to_dict() if self.doctor else None,
            'patient': self.patient.to_dict() if self.patient else None
        }
