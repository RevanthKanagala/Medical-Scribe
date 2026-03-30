"""FastAPI server to transcribe audio via OpenAI Whisper and summarize transcripts.

Endpoints:
 - POST /upload  -> accepts form file upload (audio), returns transcript text and saves a .txt
 - POST /summarize -> accepts form 'transcript' (text) or 'transcript_path' (server path) and returns summary

Reads OPENAI_API_KEY from environment or .env.
"""
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from statistics import mean, median
from pydub import AudioSegment
from typing import Optional, List, Dict
from dotenv import load_dotenv
# Force reload environment variables to get latest values
load_dotenv(override=True)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import socket
import tempfile
import subprocess
import time
import threading
import requests

from app.database import (
    upsert_doctor,
    upsert_patient,
    add_consultation_record,
    get_patient_with_history,
    get_consultation_by_id,
    generate_next_uhid,
    get_connection,
)

# Import symptom extraction pipeline
from app.symptom_pipeline import (
    extract_symptoms_from_transcript,
    approve_unknown_symptom,
    symptom_catalog
)

# AssemblyAI API key - try from environment or use hardcoded key
ASSEMBLYAI_KEY = os.getenv('ASSEMBLYAI_API_KEY', '7b1e682337af4c67afe4e8edfb0985b3')
OPENAI_KEY = os.getenv('OPENAI_API_KEY')
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', 'uploads'))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Server + tunnel configuration
SERVER_PORT = int(os.getenv('SERVER_PORT', '8000'))
ENABLE_NGROK = os.getenv('ENABLE_NGROK', 'false').lower() == 'true'
NGROK_PATH = os.getenv('NGROK_PATH', 'ngrok')
_ngrok_process = None
NGROK_URL = None

# Privacy settings
MASK_NAMES_IN_TRANSCRIPT = os.getenv('MASK_NAMES', 'true').lower() == 'true'

# Debug: Log API key status at startup
print(f"[STARTUP] OpenAI API Key loaded: {OPENAI_KEY[:10] if OPENAI_KEY else 'NOT FOUND'}...{OPENAI_KEY[-4:] if OPENAI_KEY else ''}")

app = FastAPI(title='Medical Scribe - AIMS')
static_dir = Path('web/static')
if static_dir.exists():
    app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')

# Store last summary in memory for /summary endpoint
last_summary = ""
last_symptom_extraction = {}

SUMMARY_HEADERS = [
    'CHIEF COMPLAINTS',
    'ALLERGIES',
    'DIAGNOSIS / PRE-EXISTING DISEASES',
    'MEDICINES PRESCRIBED',
    'SUGGESTIONS / ADVICE',
    'NEXT VISIT / FOLLOW-UP',
    'NEXT INVESTIGATIONS TO BE DONE',
]


def _safe_mean(values: List[float]) -> float:
    return float(mean(values)) if values else 0.0


def _safe_median(values: List[float]) -> float:
    return float(median(values)) if values else 0.0


def extract_summary_section(summary_text: Optional[str], header: str) -> List[str]:
    """Return cleaned lines from a given summary section."""
    if not summary_text:
        return []
    marker = f"{header}:"
    if marker not in summary_text:
        return []

    tail = summary_text.split(marker, 1)[1]
    stop = len(tail)
    for other in SUMMARY_HEADERS:
        if other == header:
            continue
        marker_other = f"{other}:"
        idx = tail.find(marker_other)
        if idx != -1 and idx < stop:
            stop = idx

    segment = tail[:stop]
    cleaned_lines: List[str] = []
    for raw_line in segment.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(('=', '-')):
            continue
        if stripped.upper().startswith('MEDICINE'):
            continue
        normalized = stripped.lstrip('•-').strip()
        if normalized:
            cleaned_lines.append(normalized)
    return cleaned_lines


def _normalize_phrase(value: Optional[str]) -> str:
    if not value:
        return ''
    return ''.join(ch.lower() for ch in value if ch.isalnum() or ch.isspace()).strip()


def validate_summary_against_symptoms(summary_text: Optional[str], symptom_data: Optional[dict]) -> dict:
    """Verify summary complaints align with validated symptoms."""
    symptoms_present = (symptom_data or {}).get('symptoms_present') or []
    validated_names = [s.get('name', '') for s in symptoms_present if s.get('name')]
    validated_map = {_normalize_phrase(name): name for name in validated_names if _normalize_phrase(name)}

    section_items = extract_summary_section(summary_text or '', 'CHIEF COMPLAINTS')
    matched_names: List[str] = []
    unmatched_summary_items: List[str] = []

    for item in section_items:
        normalized_item = _normalize_phrase(item)
        if not normalized_item:
            continue
        matched = None
        for norm_name, original_name in validated_map.items():
            if norm_name in normalized_item or normalized_item in norm_name:
                matched = original_name
                break
        if matched:
            if matched not in matched_names:
                matched_names.append(matched)
        else:
            unmatched_summary_items.append(item)

    missing_symptoms = [name for name in validated_names if name not in matched_names]
    is_valid = not missing_symptoms and not unmatched_summary_items

    return {
        'is_valid': is_valid,
        'validated_symptoms': validated_names,
        'matched_symptoms': matched_names,
        'missing_symptoms': missing_symptoms,
        'unmatched_summary_items': unmatched_summary_items,
        'checked_section': 'CHIEF COMPLAINTS',
    }


def _load_consultations_with_context() -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*, d.name AS doctor_name, d.department AS doctor_department,
                   p.name AS patient_name, p.uhid AS patient_uhid
            FROM consultations c
            LEFT JOIN doctors d ON c.doctor_id = d.id
            LEFT JOIN patients p ON c.patient_id = p.id
            """
        ).fetchall()

    consultations: List[Dict] = []
    for row in rows:
        visit_dt = _parse_visit_datetime(row['visit_datetime']) if row['visit_datetime'] else None
        created_dt = None
        if row['created_at']:
            try:
                created_dt = datetime.fromisoformat(row['created_at'])
            except Exception:
                created_dt = None
        try:
            symptoms_present = json.loads(row['symptoms_present'] or '[]')
        except Exception:
            symptoms_present = []
        try:
            unknown_mentions = json.loads(row['unknown_mentions'] or '[]')
        except Exception:
            unknown_mentions = []

        consultations.append({
            'id': row['id'],
            'doctor_id': row['doctor_id'],
            'doctor_name': row['doctor_name'] or 'Unknown Doctor',
            'doctor_department': row['doctor_department'],
            'patient_id': row['patient_id'],
            'patient_name': row['patient_name'] or 'Unknown Patient',
            'patient_uhid': row['patient_uhid'],
            'visit_dt': visit_dt,
            'visit_date': visit_dt.date() if visit_dt else None,
            'symptoms_present': symptoms_present,
            'unknown_mentions': unknown_mentions,
            'symptom_count': row['symptom_count'] or 0,
            'summary': row['summary_text'],
            'transcript_length': row['transcript_length'] or 0,
            'audio_path': row['audio_path'],
            'created_dt': created_dt,
        })
    return consultations


def compute_dashboard_metrics() -> Dict:
    consultations = _load_consultations_with_context()
    now = datetime.utcnow()
    today = now.date()
    seven_days_ago = today - timedelta(days=6)
    previous_week_start = today - timedelta(days=13)
    previous_week_end = today - timedelta(days=7)

    total_consults = len(consultations)
    high_risk_threshold = 4
    high_risk_cases = 0
    high_risk_today = 0
    validation_pass = 0
    validation_fail = 0
    validation_evaluations = 0
    unknown_case_count = 0
    pending_summaries = 0
    patients_today: set = set()
    completed_today = 0
    pending_today = 0
    unique_doctors_today: set = set()
    patients_per_hour = Counter()
    doc_delay_minutes: List[float] = []
    transcript_lengths: List[int] = []
    summary_lengths: List[int] = []
    symptom_counts: List[int] = []
    risk_distribution = Counter()
    counts_by_date = defaultdict(int)
    symptoms_by_date = defaultdict(list)
    patient_first_visit: Dict[int, Optional[datetime]] = {}
    patient_consult_counts = Counter()
    doctor_stats: Dict[str, Dict] = {}
    audio_records = 0
    latest_visit: Optional[datetime] = None

    for entry in consultations:
        visit_dt = entry['visit_dt']
        visit_date = entry['visit_date']
        symptom_count = entry['symptom_count'] or 0
        summary = entry['summary']
        unknown_mentions = entry['unknown_mentions'] or []
        symptoms_present = entry['symptoms_present'] or []

        if entry['patient_id'] is not None and visit_dt:
            first_visit = patient_first_visit.get(entry['patient_id'])
            if not first_visit or visit_dt < first_visit:
                patient_first_visit[entry['patient_id']] = visit_dt
        if entry['patient_id'] is not None:
            patient_consult_counts[entry['patient_id']] += 1

        if visit_dt:
            counts_by_date[visit_date] += 1
            symptoms_by_date[visit_date].append(symptom_count)
            if visit_date == today:
                patients_today.add(entry['patient_id'])
                unique_doctors_today.add(entry['doctor_id'])
                if summary:
                    completed_today += 1
                else:
                    pending_today += 1
                patients_per_hour[visit_dt.hour] += 1
            if not latest_visit or visit_dt > latest_visit:
                latest_visit = visit_dt

        if summary:
            summary_lengths.append(len(summary))
        else:
            pending_summaries += 1

        if entry['transcript_length']:
            transcript_lengths.append(entry['transcript_length'])

        if entry['created_dt'] and visit_dt:
            delay_minutes = max(0.0, (entry['created_dt'] - visit_dt).total_seconds() / 60)
            doc_delay_minutes.append(delay_minutes)

        symptom_counts.append(symptom_count)
        if symptom_count >= high_risk_threshold or unknown_mentions:
            high_risk_cases += 1
            if visit_date == today:
                high_risk_today += 1
        if unknown_mentions:
            unknown_case_count += 1

        if summary and symptoms_present:
            symptom_payload = {'symptoms_present': symptoms_present}
            validation = validate_summary_against_symptoms(summary, symptom_payload)
            validation_evaluations += 1
            if validation['is_valid']:
                validation_pass += 1
            else:
                validation_fail += 1

        for symptom in symptoms_present:
            name = symptom.get('name')
            if name:
                risk_distribution[name] += 1

        doc_key = entry['doctor_id'] if entry['doctor_id'] is not None else f"anon_{entry['doctor_name']}"
        stats = doctor_stats.setdefault(doc_key, {
            'name': entry['doctor_name'],
            'department': entry['doctor_department'],
            'patients': 0,
            'symptom_counts': [],
            'last_visit': None,
        })
        stats['patients'] += 1
        stats['symptom_counts'].append(symptom_count)
        if visit_dt and (not stats['last_visit'] or visit_dt > stats['last_visit']):
            stats['last_visit'] = visit_dt

        if entry['audio_path']:
            audio_records += 1

    patients_per_hour_list = [
        {'hour': hour, 'count': count}
        for hour, count in sorted(patients_per_hour.items())
    ]

    insights: List[str] = []
    if patients_per_hour_list:
        peak_hour = max(patients_per_hour_list, key=lambda item: item['count'])
        insights.append(f"Peak at {peak_hour['hour']:02d}:00 with {peak_hour['count']} visits")
    if pending_today:
        insights.append(f"{pending_today} visits awaiting summary completion")
    if high_risk_today:
        insights.append(f"{high_risk_today} high-risk consultations today")

    per_doctor = []
    for stats in doctor_stats.values():
        per_doctor.append({
            'name': stats['name'],
            'department': stats['department'],
            'patients': stats['patients'],
            'avg_symptoms': round(_safe_mean(stats['symptom_counts']), 1),
            'last_visit': stats['last_visit'].isoformat() if stats['last_visit'] else None,
        })
    per_doctor.sort(key=lambda item: item['patients'], reverse=True)

    overload_threshold = 10
    overloaded_doctors = [doc['name'] for doc in per_doctor if doc['patients'] >= overload_threshold]

    total_symptom_counts = [count for count in symptom_counts if count is not None]
    avg_symptom_value = round(_safe_mean(total_symptom_counts), 1) if total_symptom_counts else 0.0

    doc_delay_avg = round(_safe_mean(doc_delay_minutes), 1) if doc_delay_minutes else 0.0
    doc_delay_median = round(_safe_median(doc_delay_minutes), 1) if doc_delay_minutes else 0.0
    avg_transcript_chars = round(_safe_mean(transcript_lengths), 0) if transcript_lengths else 0.0
    avg_summary_chars = round(_safe_mean(summary_lengths), 0) if summary_lengths else 0.0

    validation_pass_rate = (
        validation_pass / validation_evaluations if validation_evaluations else None
    )

    risk_distribution_list = [
        {'label': label, 'value': value}
        for label, value in risk_distribution.most_common(5)
    ]

    last_seven_days_counts = []
    total_last_7 = 0
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        count = counts_by_date.get(day, 0)
        total_last_7 += count
        last_seven_days_counts.append({'date': day.isoformat(), 'count': count})

    previous_week_total = 0
    for offset in range(13, 6, -1):
        day = today - timedelta(days=offset)
        previous_week_total += counts_by_date.get(day, 0)

    symptom_values_7d = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        symptom_values_7d.extend(symptoms_by_date.get(day, []))
    avg_symptom_7d = round(_safe_mean(symptom_values_7d), 1) if symptom_values_7d else 0.0

    new_patients_last_7 = sum(
        1 for visit in patient_first_visit.values()
        if visit and visit.date() >= seven_days_ago
    )

    unique_patients = len(patient_consult_counts)
    repeat_patients = sum(1 for count in patient_consult_counts.values() if count > 1)
    repeat_rate = (repeat_patients / unique_patients) if unique_patients else 0

    trends_cards = [
        {
            'label': '7-Day Consultations',
            'value': total_last_7,
            'status': 'green' if total_last_7 >= previous_week_total else 'yellow'
        },
        {
            'label': 'New Patients (7d)',
            'value': new_patients_last_7,
            'status': 'green'
        },
        {
            'label': 'Avg Symptoms (7d)',
            'value': f"{avg_symptom_7d:.1f}",
            'status': 'green'
        },
        {
            'label': 'Repeat Patient Rate',
            'value': f"{repeat_rate * 100:.0f}%",
            'status': 'green' if repeat_rate <= 0.5 else 'yellow'
        }
    ]

    high_risk_cases_today = high_risk_today

    latest_visit_iso = latest_visit.isoformat() if latest_visit else None
    summaries_count = len(summary_lengths)

    return {
        'generated_at': now.isoformat(),
        'critical_alerts': {
            'high_risk_cases': high_risk_cases,
            'validation_failures': validation_fail,
            'pending_summaries': pending_summaries,
            'unknown_mention_cases': unknown_case_count,
        },
        'patient_flow': {
            'patients_today': len([pid for pid in patients_today if pid is not None]),
            'completed_today': completed_today,
            'pending_today': pending_today,
            'unique_doctors_today': len([doc for doc in unique_doctors_today if doc is not None]),
            'avg_symptom_count': avg_symptom_value,
            'patients_per_hour': patients_per_hour_list,
            'insights': insights,
        },
        'doctor_workload': {
            'per_doctor': per_doctor,
            'overloaded': overloaded_doctors,
        },
        'efficiency': {
            'avg_doc_delay_minutes': doc_delay_avg,
            'median_doc_delay_minutes': doc_delay_median,
            'avg_transcript_chars': avg_transcript_chars,
            'avg_summary_chars': avg_summary_chars,
        },
        'ai_reliability': {
            'validation_pass_rate': validation_pass_rate,
            'validation_evaluations': validation_evaluations,
            'validation_failures': validation_fail,
            'unknown_case_rate': (unknown_case_count / total_consults) if total_consults else 0,
        },
        'risk_monitoring': {
            'high_risk_today': high_risk_cases_today,
            'risk_distribution': risk_distribution_list,
            'unresolved_unknowns': unknown_case_count,
        },
        'system_health': {
            'openai_api': bool(OPENAI_KEY),
            'assemblyai_api': bool(ASSEMBLYAI_KEY),
            'summary_coverage': (summaries_count / total_consults) if total_consults else None,
            'audio_records': audio_records,
            'last_consultation': latest_visit_iso,
            'consultation_count': total_consults,
        },
        'trends': {
            'patients_last_7_days': last_seven_days_counts,
            'trends_cards': trends_cards,
        }
    }


def _get_openai_client():
    """Return (client, mode) where mode is 'modern' or 'legacy'."""
    try:
        import openai as o
    except Exception:
        raise RuntimeError('openai package not installed. Install with: pip install openai')

    if hasattr(o, 'OpenAI'):
        client = o.OpenAI(api_key=OPENAI_KEY)
        return client, 'modern'

    if OPENAI_KEY:
        o.api_key = OPENAI_KEY
    return o, 'legacy'



# Helper: convert webm to wav using ffmpeg or pydub
def convert_webm_to_wav(webm_path, wav_path):
    try:
        # Try ffmpeg first
        subprocess.run([
            'ffmpeg', '-y', '-i', str(webm_path), '-ar', '16000', '-ac', '1', str(wav_path)
        ], check=True, capture_output=True)
    except FileNotFoundError:
        # ffmpeg not found, try pydub as fallback
        print("[WARNING] ffmpeg not found, trying pydub...")
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(webm_path), format="webm")
            audio = audio.set_frame_rate(16000).set_channels(1)
            audio.export(str(wav_path), format="wav")
        except Exception as e:
            raise RuntimeError(f'Audio conversion failed. Install ffmpeg or pydub: {e}')
    except Exception as e:
        raise RuntimeError(f'ffmpeg conversion failed: {e}')

# Transcribe using AssemblyAI
def mask_patient_info(patient_name: str, uhid: str) -> tuple:
    """Mask patient information for privacy.
    
    Args:
        patient_name: Full patient name
        uhid: Unique Health ID
        
    Returns:
        Tuple of (masked_name, masked_uhid)
        - Name is completely masked with asterisks
        - UHID shows only last 4 digits (e.g., ****1234)
    """
    # Mask name completely
    masked_name = '*' * len(patient_name.replace(' ', ''))
    
    # Mask UHID - show only last 4 digits
    if len(uhid) > 4:
        masked_uhid = '*' * (len(uhid) - 4) + uhid[-4:]
    else:
        masked_uhid = uhid  # If UHID is 4 or fewer chars, show as is
    
    return masked_name, masked_uhid


def transcribe_with_assemblyai(path: str, mask_names: bool = True) -> str:
    import assemblyai as aai
    if not ASSEMBLYAI_KEY:
        raise RuntimeError('ASSEMBLYAI_API_KEY not set')
    aai.settings.api_key = ASSEMBLYAI_KEY
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(path)
    if getattr(transcript, 'error', None):
        raise RuntimeError(transcript.error)
    
    text = transcript.text
    return text


def generate_medical_summary(
    transcript: str, 
    symptom_data: dict, 
    doctor_info: dict, 
    patient_info: dict,
    max_tokens: int = 1500
) -> str:
    """Generate comprehensive medical summary with full structured format.
    
    Includes: Doctor info, Patient info, Chief Complaints, Allergies, Diagnosis,
    Medicines (tabulated), Suggestions, Next Visit
    """
    if not OPENAI_KEY:
        raise RuntimeError('OPENAI_API_KEY not configured.')
    
    client, mode = _get_openai_client()
    max_chars = 15000
    input_text = transcript[:max_chars]
    
    # Build validated symptoms list
    validated_symptoms = []
    if symptom_data and symptom_data.get('symptoms_present'):
        validated_symptoms = [s['name'] for s in symptom_data['symptoms_present']]
    
    # Prompt for comprehensive medical documentation
    prompt = f"""You are a professional medical scribe. Extract a comprehensive medical summary from the transcript.

**STRICT RULES**:
1. Only use symptoms from this validated list: {', '.join(validated_symptoms) if validated_symptoms else 'None detected'}
2. Do NOT invent any information not in the transcript
3. Extract medicines with exact dosage, timing (morning/afternoon/evening/night), and food instructions (before/after food)
4. Format medicines as a table

**TRANSCRIPT:**
{input_text}

**REQUIRED OUTPUT FORMAT** (JSON):
{{
  "chief_complaints": ["list of main complaints"],
  "allergies": ["list of allergies or 'None reported'"],
  "diagnosis": "Primary diagnosis or pre-existing diseases mentioned",
  "medicines": [
    {{
      "name": "Medicine name",
      "dosage": "mg amount",
      "morning": "1/0",
      "afternoon": "1/0",
      "evening": "1/0",
      "night": "1/0",
      "food": "before/after food"
    }}
  ],
  "suggestions": ["Advice and suggestions"],
  "next_visit": "Next visit date or follow-up instructions",
  "investigations": ["Next investigations to be done"]
}}

Extract ONLY what is mentioned. Use null for missing information."""

    def _call_openai(messages):
        print(f"[DEBUG] Calling OpenAI with mode: {mode}")
        if mode == 'modern':
            print(f"[DEBUG] Using modern OpenAI client")
            resp = client.chat.completions.create(
                model='gpt-3.5-turbo',
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return resp.choices[0].message.content
        else:
            print(f"[DEBUG] Using legacy OpenAI client")
            import openai as _openai
            resp = _openai.ChatCompletion.create(
                model='gpt-3.5-turbo',
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return resp['choices'][0]['message']['content']
    
    try:
        print(f"[DEBUG] Starting OpenAI call for medical summary...")
        raw = _call_openai([
            {"role": "system", "content": "You are a medical documentation expert. Extract only factual information."},
            {"role": "user", "content": prompt},
        ])
        print(f"[DEBUG] OpenAI call successful, response length: {len(raw) if raw else 0}")
        
        # Parse JSON response
        content = raw.strip()
        if '```' in content:
            content = content.split('```')[1].replace('json', '').strip()
        
        data = json.loads(content)
        
        # Format as structured medical report
        report = []
        report.append("="*80)
        report.append("MEDICAL CONSULTATION SUMMARY")
        report.append("="*80)
        report.append("")
        
        # Doctor Information
        if doctor_info:
            report.append("DOCTOR INFORMATION:")
            report.append(f"  Name: {doctor_info.get('name', 'N/A')}")
            report.append(f"  Department: {doctor_info.get('department', 'N/A')}")
            report.append(f"  Designation: {doctor_info.get('designation', 'N/A')}")
            report.append(f"  Patient Type: {doctor_info.get('patientType', 'N/A')}")
            report.append("")
        
        # Patient Information (with privacy masking)
        if patient_info:
            patient_name = patient_info.get('name', 'N/A')
            patient_uhid = patient_info.get('uhid', 'N/A')
            
            # Apply masking if enabled
            if MASK_NAMES_IN_TRANSCRIPT and patient_name != 'N/A' and patient_uhid != 'N/A':
                masked_name, masked_uhid = mask_patient_info(patient_name, patient_uhid)
                report.append("PATIENT INFORMATION:")
                report.append(f"  Name: {masked_name} (masked for privacy)")
                report.append(f"  UHID: {masked_uhid}")
            else:
                report.append("PATIENT INFORMATION:")
                report.append(f"  Name: {patient_name}")
                report.append(f"  UHID: {patient_uhid}")
            
            report.append(f"  Sex: {patient_info.get('sex', 'N/A')}")
            report.append(f"  Age: {patient_info.get('age', 'N/A')} years")
            report.append(f"  Date of Birth: {patient_info.get('dob', 'N/A')}")
            report.append(f"  Visit Date/Time: {patient_info.get('visitDateTime', 'N/A')}")
            unit_suite = patient_info.get('unitSuite') or patient_info.get('unit_suite')
            street = patient_info.get('street')
            city = patient_info.get('city')
            province = patient_info.get('province')
            postal_code = patient_info.get('postalCode') or patient_info.get('postal_code')
            address_line = patient_info.get('address')
            address_parts = [part for part in [unit_suite, street, city, province, postal_code] if part]
            if address_parts:
                report.append(f"  Address: {', '.join(address_parts)}")
            elif address_line:
                report.append(f"  Address: {address_line}")
            report.append("")
        
        # Chief Complaints
        report.append("CHIEF COMPLAINTS:")
        if data.get('chief_complaints'):
            for cc in data['chief_complaints']:
                report.append(f"  • {cc}")
        else:
            report.append("  None documented")
        report.append("")
        
        # Allergies
        report.append("ALLERGIES:")
        if data.get('allergies'):
            for allergy in data['allergies']:
                report.append(f"  • {allergy}")
        else:
            report.append("  None reported")
        report.append("")
        
        # Diagnosis / Pre-existing Diseases
        report.append("DIAGNOSIS / PRE-EXISTING DISEASES:")
        report.append(f"  {data.get('diagnosis', 'Not documented')}")
        report.append("")
        
        # Medicines (Tabulated)
        report.append("MEDICINES PRESCRIBED:")
        if data.get('medicines') and len(data['medicines']) > 0:
            report.append("-"*80)
            report.append(f"{'Medicine':<25} {'Dosage':<10} {'Morning':<8} {'Afternoon':<10} {'Evening':<8} {'Night':<7} {'Food':<12}")
            report.append("-"*80)
            for med in data['medicines']:
                report.append(
                    f"{med.get('name', 'N/A'):<25} "
                    f"{med.get('dosage', 'N/A'):<10} "
                    f"{med.get('morning', '0'):<8} "
                    f"{med.get('afternoon', '0'):<10} "
                    f"{med.get('evening', '0'):<8} "
                    f"{med.get('night', '0'):<7} "
                    f"{med.get('food', 'N/A'):<12}"
                )
            report.append("-"*80)
        else:
            report.append("  No medicines prescribed")
        report.append("")
        
        # Suggestions / Advice
        report.append("SUGGESTIONS / ADVICE:")
        if data.get('suggestions'):
            for suggestion in data['suggestions']:
                report.append(f"  • {suggestion}")
        else:
            report.append("  None documented")
        report.append("")
        
        # Next Visit / Investigations
        report.append("NEXT VISIT / FOLLOW-UP:")
        report.append(f"  {data.get('next_visit', 'Not scheduled')}")
        report.append("")
        
        report.append("NEXT INVESTIGATIONS TO BE DONE:")
        if data.get('investigations'):
            for inv in data['investigations']:
                report.append(f"  • {inv}")
        else:
            report.append("  None recommended")
        report.append("")
        
        report.append("="*80)
        report.append(f"Generated by AI Medical Scribe | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("="*80)
        
        return "\n".join(report)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Failed to generate medical summary: {e}")
        print(f"[ERROR] Full traceback:\n{error_details}")
        # Fallback to basic summary
        return f"""
MEDICAL SUMMARY
Doctor: {doctor_info.get('name', 'N/A')}
Patient: {patient_info.get('name', 'N/A')} (UHID: {patient_info.get('uhid', 'N/A')})
Visit: {patient_info.get('visitDateTime', 'N/A')}

TRANSCRIPT:
{transcript}

Note: Automated summary generation failed. Manual review required.
Error: {str(e)}
"""


def generate_followup_questions(
    *,
    transcript: Optional[str],
    summary: Optional[str],
    visit_datetime: Optional[str],
    doctor_name: Optional[str],
    doctor_department: Optional[str],
    max_questions: int = 5,
) -> List[str]:
    """Create focused follow-up questions for the next visit."""
    def _fallback_questions() -> List[str]:
        fallback: List[str] = []
        summary_text = (summary or '').strip()
        def _push(candidate: Optional[str]):
            if not candidate:
                return
            clean = candidate.strip()
            if clean and clean not in fallback:
                fallback.append(clean)

        if summary_text:
            complaints = [line.rstrip('.') for line in extract_summary_section(summary_text, 'CHIEF COMPLAINTS') if not line.lower().startswith('none')]
            for complaint in complaints:
                _push(f"How have your {complaint.lower()} progressed since the last visit?")

            suggestions = extract_summary_section(summary_text, 'SUGGESTIONS / ADVICE')
            for suggestion in suggestions:
                _push(f"Were you able to follow the advice about {suggestion.lower()}?")

            followups = extract_summary_section(summary_text, 'NEXT VISIT / FOLLOW-UP')
            for item in followups:
                _push(f"Do we need to adjust the follow-up plan regarding {item.lower()}?")

            investigations = extract_summary_section(summary_text, 'NEXT INVESTIGATIONS TO BE DONE')
            for inv in investigations:
                _push(f"Have you scheduled the recommended investigation: {inv}?")

        if transcript and len(fallback) < max_questions:
            first_sentences = transcript.strip().split('.')[:2]
            for sentence in first_sentences:
                snippet = sentence.strip()
                if snippet:
                    _push(f"Any updates regarding '{snippet}' since the last consultation?")

        generic_pool = [
            'Have there been any new symptoms or concerns since our last consultation?',
            'Are the prescribed medicines causing any side effects or challenges?',
            'How are you feeling overall compared to the previous visit?',
            'Is the current treatment plan manageable for you day to day?',
        ]
        for generic in generic_pool:
            if len(fallback) >= max_questions:
                break
            _push(generic)

        return fallback[:max_questions]

    context_chunks = []
    if summary:
        context_chunks.append(f"Previous Summary:\n{summary.strip()}")
    if transcript:
        snippet = transcript.strip()
        snippet = snippet[:4000]
        context_chunks.append(f"Transcript Excerpt:\n{snippet}")

    if not context_chunks:
        return _fallback_questions()

    visit_bits = []
    if visit_datetime:
        visit_bits.append(f"Visit Date: {visit_datetime}")
    if doctor_name:
        visit_bits.append(f"Consulted Doctor: {doctor_name}")
    if doctor_department:
        visit_bits.append(f"Department: {doctor_department}")

    visit_context = "\n".join(visit_bits)
    prompt = f"""A doctor is preparing for the patient's next appointment. Based on the last consultation details below, craft {max_questions} concise follow-up questions that reference prior complaints, treatments, or advice. Questions must:
1. Stay within medical scope.
2. Reference concrete details from the provided context.
3. Be phrased conversationally so the doctor can ask them verbatim.
4. Avoid mentioning the doctor's or patient's name explicitly.

Return ONLY a JSON array of strings, for example:
["How have your morning fevers been this week?", "Are you still taking the prescribed antibiotics twice a day?"]

Context:
{visit_context}
{os.linesep.join(context_chunks)}
"""

    client = mode = None
    if OPENAI_KEY:
        client, mode = _get_openai_client()
    else:
        print('[FOLLOWUP] OPENAI_API_KEY missing; using fallback questions only.')

    def _call_openai(messages):
        if not client or not mode:
            return None
        if mode == 'modern':
            resp = client.chat.completions.create(
                model='gpt-3.5-turbo',
                messages=messages,
                max_tokens=256,
                temperature=0.4,
            )
            return resp.choices[0].message.content
        import openai as _openai
        resp = _openai.ChatCompletion.create(
            model='gpt-3.5-turbo',
            messages=messages,
            max_tokens=256,
            temperature=0.4,
        )
        return resp['choices'][0]['message']['content']

    def _parse_questions(raw: str) -> List[str]:
        if not raw:
            return []
        cleaned = raw.strip()
        if '```' in cleaned:
            parts = cleaned.split('```')
            if len(parts) >= 2:
                cleaned = parts[1].replace('json', '').strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                if 'questions' in data and isinstance(data['questions'], list):
                    data = data['questions']
                else:
                    data = list(data.values())
            if isinstance(data, list):
                return [str(q).strip() for q in data if str(q).strip()]
        except Exception:
            pass
        # Fallback: split lines
        fallback = []
        for line in cleaned.splitlines():
            line = line.strip().lstrip('-').strip()
            if line:
                fallback.append(line)
            if len(fallback) >= max_questions:
                break
        return fallback

    questions: List[str] = []
    if client and mode:
        try:
            raw = _call_openai([
                {
                    'role': 'system',
                    'content': 'You craft medically appropriate follow-up questions based solely on provided consultation notes.'
                },
                {'role': 'user', 'content': prompt},
            ])
            questions = _parse_questions(raw)
        except Exception as exc:
            print(f"[FOLLOWUP] Failed to generate follow-up questions: {exc}")

    if len(questions) < max_questions:
        fallback = _fallback_questions()
        for candidate in fallback:
            if len(questions) >= max_questions:
                break
            if candidate not in questions:
                questions.append(candidate)

    return questions[:max_questions]


def summarize_with_openai(text: str, symptom_data: dict = None, max_tokens: int = 512) -> str:
    """Summarize using ONLY validated symptoms (AIMS-safe).
    
    Args:
        text: Raw transcript
        symptom_data: Result from extract_symptoms_from_transcript() with symptoms_present
        max_tokens: OpenAI max token limit
    
    Returns:
        Clinical summary using only validated symptoms from catalog
    """
    if not OPENAI_KEY:
        raise RuntimeError('OPENAI_API_KEY not configured. Please set it in your .env file or environment variables.')
    
    client, mode = _get_openai_client()
    # Trim extremely long inputs to keep cost/latency sane
    max_chars = 12000
    input_text = text[:max_chars]
    
    # Build validated symptoms context for GPT
    validated_symptoms_text = ""
    if symptom_data and symptom_data.get('symptoms_present'):
        symptom_list = [f"- {s['name']} (Code: {s['code']}, Category: {s['category']})" 
                       for s in symptom_data['symptoms_present']]
        validated_symptoms_text = "\n\nVALIDATED SYMPTOMS EXTRACTED:\n" + "\n".join(symptom_list)
        validated_symptoms_text += "\n\n**CRITICAL**: Your summary MUST ONLY reference the symptoms listed above. Do NOT mention any symptoms not in this validated list."

    extraction_prompt = (
        "Extract a structured, factual summary from the following medical transcript.\n"
        "Return STRICT JSON with these nullable fields only (use null if not explicitly present, do not infer):\n"
        "{\n  \"chief_complaint\": string|null,\n  \"history\": string|null,\n  \"exam\": string|null,\n  \"assessment\": string|null,\n  \"plan\": string|null\n}\n"
        "Rules: Do not invent information. Base everything strictly on the transcript.\n"
        "**AIMS PROTOCOL**: You may ONLY reference symptoms from the VALIDATED SYMPTOMS list below. Do NOT mention symptoms not in this list.\n"
        + validated_symptoms_text + "\n\n"
        "Transcript:\n" + input_text
    )

    def _format_from_json(payload: dict) -> str:
        bullets = []
        if payload.get('chief_complaint'):
            bullets.append(f"- Chief Complaint: {payload['chief_complaint']}")
        if payload.get('history'):
            bullets.append(f"- History: {payload['history']}")
        if payload.get('exam'):
            bullets.append(f"- Exam/Observations: {payload['exam']}")
        if payload.get('assessment'):
            bullets.append(f"- Assessment: {payload['assessment']}")
        if payload.get('plan'):
            bullets.append(f"- Plan: {payload['plan']}")
        # If nothing was extracted, do a minimal extractive fallback
        if not bullets:
            bullets = simple_extractive_summary(input_text)
            bullets = [f"- {b}" for b in bullets]
        return "\n".join(bullets)

    def _call_openai(messages):
        if mode == 'modern':
            resp = client.chat.completions.create(
                model='gpt-3.5-turbo',
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
            )
            choice = resp.choices[0]
            return getattr(choice.message, 'content', None) or (choice['message']['content'] if isinstance(choice, dict) else None)
        else:
            import openai as _openai
            resp = _openai.ChatCompletion.create(
                model='gpt-3.5-turbo',
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
            )
            return resp['choices'][0]['message']['content']

    # Step 1: Ask for JSON extraction
    try:
        raw = _call_openai([
            {"role": "system", "content": "You are a careful medical scribe assistant. Only state facts in the transcript."},
            {"role": "user", "content": extraction_prompt},
        ])
        content = (raw or '').strip()
        # Attempt to find JSON within content
        json_str = content
        # If the model returned code fences or text, try to locate the JSON object
        if '```' in content:
            try:
                json_str = content.split('```')[1]
            except Exception:
                json_str = content
        data = json.loads(json_str)
        if isinstance(data, dict):
            return _format_from_json(data)
    except Exception as e:
        print(f"[WARN] JSON extraction failed, falling back to direct summary: {e}")

    # Step 2: Fallback to direct concise bullets based strictly on transcript
    try:
        fallback_prompt = (
            "Based strictly on the transcript below, write 3-7 concise factual bullets. "
            "Do NOT add information not present.\n\nTranscript:\n" + input_text
        )
        result = _call_openai([
            {"role": "system", "content": "You are a careful medical scribe assistant. Only state facts in the transcript."},
            {"role": "user", "content": fallback_prompt},
        ])
        return (result or '').strip()
    except Exception as e:
        print(f"[ERROR] OpenAI summarization failed: {e}")
        # Final fallback: naive extractive
        bullets = simple_extractive_summary(input_text)
        return "\n".join(f"- {b}" for b in bullets)


def simple_extractive_summary(text: str, max_sentences: int = 5):
    import re
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    return sentences[:max_sentences]


@app.get('/')
def index():
    if static_dir.exists():
        return RedirectResponse(url='/static/index.html')
    return {'status': 'OpenAI transcription server', 'upload_dir': str(UPLOAD_DIR)}

# Print server IP/URL on startup
# Updated to include the correct server URL with port

def print_server_url():
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        print(f"\nServer running! Access the web UI at: http://{ip}:{SERVER_PORT}/static/index.html\n")
        return f"http://{ip}:{SERVER_PORT}/static/index.html"
    except Exception:
        print(f"\nServer running! Access the web UI at: http://localhost:{SERVER_PORT}/static/index.html\n")
        return f"http://localhost:{SERVER_PORT}/static/index.html"

# Start the thread to print the server URL
server_url = print_server_url()
threading.Thread(target=print_server_url, daemon=True).start()


def _fetch_existing_ngrok_url():
    try:
        resp = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2)
        tunnels = resp.json().get('tunnels', [])
        for tunnel in tunnels:
            if tunnel.get('proto') == 'https':
                return tunnel.get('public_url')
    except Exception as exc:
        print(f"[NGROK] Unable to query existing tunnel: {exc}")
    return None


def start_ngrok_tunnel(port: int = SERVER_PORT):
    """Start ngrok automatically and log the public URL."""
    global _ngrok_process, NGROK_URL
    if not ENABLE_NGROK:
        return

    if NGROK_URL:
        return

    # Re-use active tunnel if one exists
    existing_url = _fetch_existing_ngrok_url()
    if existing_url:
        NGROK_URL = existing_url
        print(f"[NGROK] Existing public URL: {NGROK_URL}")
        return

    try:
        _ngrok_process = subprocess.Popen(
            [NGROK_PATH, 'http', str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(f"[NGROK] Executable '{NGROK_PATH}' not found. Set NGROK_PATH env var.")
        return
    except Exception as exc:
        print(f"[NGROK] Failed to launch ngrok: {exc}")
        return

    # Poll the local API for a public URL
    for _ in range(20):
        time.sleep(1)
        url = _fetch_existing_ngrok_url()
        if url:
            NGROK_URL = url
            print(f"[NGROK] Tunnel established: {NGROK_URL}")
            print(f"[NGROK] Web UI: {NGROK_URL}/static/index.html")
            return

    print("[NGROK] Timed out waiting for public URL. Check ngrok logs.")


if ENABLE_NGROK:
    threading.Thread(target=start_ngrok_tunnel, daemon=True).start()


def _parse_visit_datetime(value: Optional[str]) -> datetime:
    if not value:
        return datetime.utcnow()
    known_formats = [
        "%d/%m/%Y, %I:%M:%S %p",
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d-%b-%Y, %I:%M:%S %p",
    ]
    for fmt in known_formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.utcnow()



@app.post('/upload')
async def upload_audio(file: UploadFile = File(...)):
    orig_name = file.filename or 'audio.webm'
    content_type = getattr(file, 'content_type', '') or ''
    print(f"[DEBUG] Received file: {orig_name}, content_type: {content_type}")
    
    if not ASSEMBLYAI_KEY:
        return JSONResponse({'error': 'ASSEMBLYAI_API_KEY not configured'}, status_code=500)

    # Name files using a timestamp (milliseconds) as the base, e.g., 1730918532345.wav/.txt
    base_ts = str(int(time.time() * 1000))
    ext = Path(orig_name).suffix.lower()
    if not ext:
        if content_type.endswith('/webm'):
            ext = '.webm'
        else:
            ext = '.wav'
    audio_name = f"{base_ts}{ext}"
    save_path = UPLOAD_DIR / audio_name
    contents = await file.read()
    save_path.write_bytes(contents)
    
    print(f"[DEBUG] Saved to: {save_path}, size: {len(contents)} bytes")

    try:
        # If webm, convert to wav for AssemblyAI
        if ext == '.webm' or content_type == 'audio/webm':
            print("[DEBUG] Detected webm audio, converting to wav...")
            wav_path = UPLOAD_DIR / f"{base_ts}.wav"
            try:
                convert_webm_to_wav(save_path, wav_path)
                print(f"[DEBUG] Conversion successful, transcribing {wav_path}...")
                text = await run_in_threadpool(transcribe_with_assemblyai, str(wav_path), MASK_NAMES_IN_TRANSCRIPT)
                # Clean up wav file after transcription
                if wav_path.exists():
                    os.remove(wav_path)
            except Exception as conv_error:
                print(f"[ERROR] Conversion error: {conv_error}")
                raise
        else:
            print(f"[DEBUG] Transcribing {save_path} directly...")
            text = await run_in_threadpool(transcribe_with_assemblyai, str(save_path), MASK_NAMES_IN_TRANSCRIPT)
        
        print(f"[DEBUG] Transcription successful! Text length: {len(text)} chars")
        print(f"[DEBUG] First 100 chars: {text[:100]}...")
        
        txt_path = UPLOAD_DIR / f"{base_ts}.txt"
        txt_path.write_text(text, encoding='utf-8')
        print(f"[DEBUG] Transcript saved to: {txt_path}")
        return {'transcript': text, 'transcript_path': str(txt_path), 'audio_path': str(save_path)}
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Transcription failed: {str(e)}")
        print(f"[ERROR] Full traceback:\n{error_details}")
        return JSONResponse({'error': f'Transcription failed: {str(e)}'}, status_code=500)



@app.post('/extract_symptoms')
async def extract_symptoms_endpoint(transcript: str = Form(...)):
    """Extract symptoms using AIMS pipeline (no hallucinations)."""
    global last_symptom_extraction
    
    if not transcript or not transcript.strip():
        return JSONResponse({'error': 'Transcript is empty'}, status_code=400)
    
    try:
        print(f"[DEBUG] Extracting symptoms from {len(transcript)} char transcript...")
        result = await run_in_threadpool(extract_symptoms_from_transcript, transcript)
        last_symptom_extraction = result
        
        print(f"[DEBUG] Found {result['symptom_count']} known symptoms, {result['unknown_count']} unknown mentions")
        if result['symptoms_present']:
            print(f"[DEBUG] Known symptoms: {[s['name'] for s in result['symptoms_present']]}")
        if result['unknown_mentions']:
            print(f"[DEBUG] Unknown mentions: {result['unknown_mentions']}")
        
        return result
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Symptom extraction failed: {str(e)}")
        print(f"[ERROR] Full traceback:\n{error_details}")
        return JSONResponse({'error': f'Symptom extraction failed: {str(e)}'}, status_code=500)


@app.post('/summarize')
async def summarize_endpoint(
    transcript: str = Form(...),
    doctor_info: str = Form(None),
    patient_info: str = Form(None),
    audio_path: str = Form(None)
):
    """Generate comprehensive medical summary with doctor and patient information."""
    global last_summary, last_symptom_extraction

    print(f"[DEBUG] Summarize request received. transcript length: {len(transcript) if transcript else 'None'}")

    if not OPENAI_KEY:
        error_msg = 'OPENAI_API_KEY not configured. Please set OPENAI_API_KEY in your environment or .env file.'
        print(f"[ERROR] {error_msg}")
        return JSONResponse({'error': error_msg}, status_code=500)

    if not transcript or not transcript.strip():
        return JSONResponse({'error': 'Transcript is empty'}, status_code=400)

    try:
        # Parse doctor and patient info
        doctor_data = json.loads(doctor_info) if doctor_info else {}
        patient_data = json.loads(patient_info) if patient_info else {}
        
        # Step 1: Extract symptoms using AIMS pipeline
        print(f"[DEBUG] Step 1: Extracting validated symptoms...")
        symptom_result = await run_in_threadpool(extract_symptoms_from_transcript, transcript)
        last_symptom_extraction = symptom_result
        
        # Step 2: Generate comprehensive medical summary
        print(f"[DEBUG] Step 2: Generating medical summary with {symptom_result['symptom_count']} validated symptoms...")
        summary = await run_in_threadpool(
            generate_medical_summary, 
            transcript, 
            symptom_result, 
            doctor_data, 
            patient_data
        )
        last_summary = summary
        validation_result = validate_summary_against_symptoms(summary, symptom_result)
        
        print(f"[DEBUG] Summary generated successfully! Length: {len(summary)} chars")
        
        # Step 3: Persist consultation data
        consultation_id = None
        try:
            doctor = upsert_doctor(doctor_data)
            patient = upsert_patient(patient_data)
            if doctor and patient:
                consultation_id = add_consultation_record(
                    doctor_id=doctor['id'],
                    patient_id=patient['id'],
                    visit_datetime=_parse_visit_datetime(patient_data.get('visitDateTime')),
                    transcript=transcript,
                    summary=summary,
                    symptoms_present=symptom_result['symptoms_present'],
                    symptom_count=symptom_result['symptom_count'],
                    unknown_mentions=symptom_result['unknown_mentions'],
                    audio_path=audio_path,
                )
        except Exception as db_error:
            print(f"[DB] Failed to persist consultation: {db_error}")

        # Return summary + symptom data
        response = {
            'summary': summary,
            'symptoms_present': symptom_result['symptoms_present'],
            'unknown_mentions': symptom_result['unknown_mentions'],
            'symptom_count': symptom_result['symptom_count'],
            'consultation_id': consultation_id,
            'summary_validation': validation_result,
        }
        
        return response
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Summarization failed: {str(e)}")
        print(f"[ERROR] Full traceback:\n{error_details}")
        return JSONResponse({'error': f'Summarization failed: {str(e)}'}, status_code=500)

# Endpoint to view last summary
@app.get('/summary')
def get_last_summary():
    global last_summary
    return HTMLResponse(f"<h2>Latest Summary</h2><pre>{last_summary}</pre>")


@app.get('/unknown_symptoms')
def get_unknown_symptoms():
    """Get list of unknown symptom mentions for human review."""
    global last_symptom_extraction
    if not last_symptom_extraction:
        return {'unknown_mentions': [], 'count': 0}
    
    return {
        'unknown_mentions': last_symptom_extraction.get('unknown_mentions', []),
        'count': last_symptom_extraction.get('unknown_count', 0)
    }


@app.get('/dashboard_metrics')
def dashboard_metrics():
    try:
        return compute_dashboard_metrics()
    except Exception as exc:
        return JSONResponse({'error': f'Unable to build dashboard metrics: {exc}'}, status_code=500)


# ==================== Database CRUD Endpoints ====================

@app.post('/doctors')
def create_or_get_doctor(
    name: str = Form(...),
    department: str = Form(...),
    designation: str = Form(...),
    patient_type: str = Form(None)
):
    """Create or retrieve a doctor by their details."""
    try:
        doctor = upsert_doctor({
            'name': name,
            'department': department,
            'designation': designation,
            'patientType': patient_type
        })
        if not doctor:
            return JSONResponse({'error': 'Doctor information incomplete'}, status_code=400)
        return {'status': 'success', 'doctor': doctor}
    except Exception as e:
        return JSONResponse({'error': f'Doctor creation failed: {e}'}, status_code=500)


@app.post('/patients')
def create_or_update_patient(
    name: str = Form(...),
    sex: str = Form(...),
    age: str = Form(...),
    dob: str = Form(...),
    phone: str = Form(None),
    email: str = Form(None),
    unit_suite: str = Form(None),
    street: str = Form(...),
    city: str = Form(...),
    province: str = Form(...),
    postal_code: str = Form(...),
    address: str = Form(None),
    existing_uhid: str = Form(None)
):
    """Create new patient or update existing patient by UHID."""
    try:
        uhid = existing_uhid or generate_next_uhid()
        payload = {
            'uhid': uhid,
            'name': name,
            'sex': sex,
            'age': age,
            'dob': dob,
            'phone': phone,
            'email': email,
            'unit_suite': unit_suite,
            'street': street,
            'city': city,
            'province': province,
            'postal_code': postal_code,
            'address': address,
        }
        patient = upsert_patient(payload)
        if not patient:
            return JSONResponse({'error': 'Patient information incomplete'}, status_code=400)
        return {'status': 'success', 'patient': patient}
    except Exception as e:
        return JSONResponse({'error': f'Patient save failed: {e}'}, status_code=500)


@app.get('/patients/{uhid}')
def get_patient_by_uhid(uhid: str):
    """Get patient details and consultation history by UHID."""
    result = get_patient_with_history(uhid)
    if not result:
        return JSONResponse({'error': 'Patient not found'}, status_code=404)

    patient, history = result
    follow_up_questions: List[str] = []
    if history:
        latest_consultation = get_consultation_by_id(history[0]['id'])
        if latest_consultation:
            doctor_meta = latest_consultation.get('doctor') or {}
            follow_up_questions = generate_followup_questions(
                transcript=latest_consultation.get('transcript'),
                summary=latest_consultation.get('summary'),
                visit_datetime=latest_consultation.get('visit_datetime'),
                doctor_name=doctor_meta.get('name'),
                doctor_department=doctor_meta.get('department'),
            )
            if follow_up_questions:
                history[0]['follow_up_questions'] = follow_up_questions
    return {
        'patient': patient,
        'total_consultations': len(history),
        'consultations': history,
        'follow_up_questions': follow_up_questions,
    }


@app.get('/consultations/{consultation_id}')
def get_consultation(consultation_id: int):
    """Get full consultation details including transcript and summary."""
    consultation = get_consultation_by_id(consultation_id)
    if not consultation:
        return JSONResponse({'error': 'Consultation not found'}, status_code=404)
    return consultation


@app.post('/approve_symptom')
async def approve_symptom_endpoint(
    mention: str = Form(...),
    code: str = Form(...),
    name: str = Form(...),
    category: str = Form(...)
):
    """Human approval: Add unknown symptom mention to catalog."""
    try:
        # Use existing AIMS pipeline function
        result = await run_in_threadpool(
            approve_unknown_symptom,
            mention=mention,
            new_code=code,
            new_name=name,
            category=category
        )
        
        if result['status'] == 'success':
            print(f"[DEBUG] Approved symptom: {name} (Code: {code})")
            return result
        else:
            return JSONResponse(result, status_code=400)
            
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Symptom approval failed: {str(e)}")
        print(f"[ERROR] Full traceback:\n{error_details}")
        return JSONResponse({'error': f'Approval failed: {str(e)}'}, status_code=500)
