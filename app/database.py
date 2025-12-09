"""Simple sqlite3 helpers for Medical Scribe data."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / 'medical_scribe.db'


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    create_script = """
    CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT,
        designation TEXT,
        patient_type TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(name, department, designation, patient_type)
    );

    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        uhid TEXT NOT NULL UNIQUE,
        sex TEXT,
        age TEXT,
        dob TEXT,
        phone TEXT,
        email TEXT,
        unit_suite TEXT,
        street TEXT,
        city TEXT,
        province TEXT,
        postal_code TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        address TEXT
    );

    CREATE TRIGGER IF NOT EXISTS patients_updated_at
    AFTER UPDATE ON patients
    FOR EACH ROW
    BEGIN
        UPDATE patients SET updated_at = CURRENT_TIMESTAMP WHERE id = old.id;
    END;

    CREATE TABLE IF NOT EXISTS consultations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doctor_id INTEGER NOT NULL,
        patient_id INTEGER NOT NULL,
        visit_datetime TEXT NOT NULL,
        transcript_text TEXT,
        transcript_length INTEGER,
        summary_text TEXT,
        symptoms_present TEXT,
        symptom_count INTEGER DEFAULT 0,
        unknown_mentions TEXT,
        audio_path TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(doctor_id) REFERENCES doctors(id),
        FOREIGN KEY(patient_id) REFERENCES patients(id)
    );
    """
    with get_connection() as conn:
        conn.executescript(create_script)


_init_db()


def _ensure_patient_columns():
    required_columns = {
        'unit_suite': 'TEXT',
        'street': 'TEXT',
        'city': 'TEXT',
        'province': 'TEXT',
        'postal_code': 'TEXT',
    }
    with get_connection() as conn:
        existing = {row['name'] for row in conn.execute('PRAGMA table_info(patients)')}
        for column, column_type in required_columns.items():
            if column not in existing:
                conn.execute(f'ALTER TABLE patients ADD COLUMN {column} {column_type}')


_ensure_patient_columns()


def _generate_next_uhid(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """SELECT uhid FROM patients 
            WHERE uhid LIKE 'AIMS%'
            ORDER BY LENGTH(uhid) DESC, uhid DESC
            LIMIT 1"""
    ).fetchone()
    if not row or not row['uhid'][4:].isdigit():
        return 'AIMS0001'
    next_number = int(row['uhid'][4:]) + 1
    return f"AIMS{next_number:04d}"


def generate_next_uhid() -> str:
    with get_connection() as conn:
        return _generate_next_uhid(conn)


def upsert_doctor(data: Optional[dict]) -> Optional[Dict]:
    if not data or not data.get('name'):
        return None

    params = (
        data.get('name'),
        data.get('department'),
        data.get('designation'),
        data.get('patientType'),
    )
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM doctors
                WHERE name = ?
                AND COALESCE(department, '') = COALESCE(?, '')
                AND COALESCE(designation, '') = COALESCE(?, '')
                AND COALESCE(patient_type, '') = COALESCE(?, '')""",
            params,
        ).fetchone()
        if row:
            return dict(row)

        conn.execute(
            """INSERT INTO doctors (name, department, designation, patient_type, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (*params, datetime.utcnow().isoformat()),
        )
        doc_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        new_row = conn.execute('SELECT * FROM doctors WHERE id = ?', (doc_id,)).fetchone()
        return dict(new_row) if new_row else None


def upsert_patient(data: Optional[dict]) -> Optional[Dict]:
    if not data or not data.get('name'):
        return None

    with get_connection() as conn:
        uhid = data.get('uhid') or _generate_next_uhid(conn)
        row = conn.execute('SELECT * FROM patients WHERE uhid = ?', (uhid,)).fetchone()
        payload = (
            data.get('name'),
            data.get('sex'),
            data.get('age'),
            data.get('dob'),
            data.get('phone'),
            data.get('email'),
            data.get('unit_suite'),
            data.get('street'),
            data.get('city'),
            data.get('province'),
            data.get('postal_code'),
            data.get('address'),
            uhid,
        )
        if row:
            conn.execute(
                """UPDATE patients
                    SET name=?, sex=?, age=?, dob=?, phone=?, email=?,
                        unit_suite=?, street=?, city=?, province=?, postal_code=?, address=?
                    WHERE uhid=?""",
                payload,
            )
        else:
            conn.execute(
                """INSERT INTO patients (
                        name, sex, age, dob, phone, email,
                        unit_suite, street, city, province, postal_code, address,
                        uhid, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data.get('name'),
                    data.get('sex'),
                    data.get('age'),
                    data.get('dob'),
                    data.get('phone'),
                    data.get('email'),
                    data.get('unit_suite'),
                    data.get('street'),
                    data.get('city'),
                    data.get('province'),
                    data.get('postal_code'),
                    data.get('address'),
                    uhid,
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )

        updated = conn.execute('SELECT * FROM patients WHERE uhid = ?', (uhid,)).fetchone()
        return dict(updated) if updated else None


def add_consultation_record(
    *,
    doctor_id: int,
    patient_id: int,
    visit_datetime: datetime,
    transcript: str,
    summary: str,
    symptoms_present: Optional[List[dict]],
    symptom_count: int,
    unknown_mentions: Optional[List[dict]],
    audio_path: Optional[str],
) -> Optional[int]:
    visit_str = visit_datetime.isoformat() if isinstance(visit_datetime, datetime) else str(visit_datetime)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO consultations (
                doctor_id, patient_id, visit_datetime, transcript_text, transcript_length,
                summary_text, symptoms_present, symptom_count, unknown_mentions, audio_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doctor_id,
                patient_id,
                visit_str,
                transcript,
                len(transcript) if transcript else None,
                summary,
                json.dumps(symptoms_present or []),
                symptom_count,
                json.dumps(unknown_mentions or []),
                audio_path,
                datetime.utcnow().isoformat(),
            ),
        )
        consult_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        return consult_id


def get_patient_with_history(uhid: str) -> Optional[Tuple[Dict, List[Dict]]]:
    with get_connection() as conn:
        patient = conn.execute('SELECT * FROM patients WHERE uhid = ?', (uhid,)).fetchone()
        if not patient:
            return None

        consultations = conn.execute(
            """SELECT c.*, d.name AS doctor_name, d.department AS doctor_department
            FROM consultations c
            LEFT JOIN doctors d ON c.doctor_id = d.id
            WHERE c.patient_id = ?
            ORDER BY c.visit_datetime DESC""",
            (patient['id'],),
        ).fetchall()

        history: List[Dict] = []
        for row in consultations:
            history.append({
                'id': row['id'],
                'visit_datetime': row['visit_datetime'],
                'doctor_name': row['doctor_name'],
                'doctor_department': row['doctor_department'],
                'symptom_count': row['symptom_count'],
                'summary': row['summary_text'],
            })

        return dict(patient), history


def get_consultation_by_id(consultation_id: int) -> Optional[Dict]:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT c.*, d.name AS doctor_name, d.department AS doctor_department,
                       d.designation AS doctor_designation,
                       p.name AS patient_name, p.uhid AS patient_uhid
            FROM consultations c
            LEFT JOIN doctors d ON c.doctor_id = d.id
            LEFT JOIN patients p ON c.patient_id = p.id
            WHERE c.id = ?""",
            (consultation_id,),
        ).fetchone()
        if not row:
            return None

        return {
            'id': row['id'],
            'visit_datetime': row['visit_datetime'],
            'doctor': {
                'id': row['doctor_id'],
                'name': row['doctor_name'],
                'department': row['doctor_department'],
                'designation': row['doctor_designation'],
            },
            'patient': {
                'id': row['patient_id'],
                'name': row['patient_name'],
                'uhid': row['patient_uhid'],
            },
            'transcript': row['transcript_text'],
            'summary': row['summary_text'],
            'symptom_count': row['symptom_count'],
            'symptoms_present': json.loads(row['symptoms_present'] or '[]'),
            'unknown_mentions': json.loads(row['unknown_mentions'] or '[]'),
            'audio_path': row['audio_path'],
        }
