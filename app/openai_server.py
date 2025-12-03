"""FastAPI server to transcribe audio via OpenAI Whisper and summarize transcripts.

Endpoints:
 - POST /upload  -> accepts form file upload (audio), returns transcript text and saves a .txt
 - POST /summarize -> accepts form 'transcript' (text) or 'transcript_path' (server path) and returns summary

Reads OPENAI_API_KEY from environment or .env.
"""
import os
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
# Force reload environment variables to get latest values
load_dotenv(override=True)

from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import socket
import tempfile
import subprocess
import time
from uuid import uuid4
import json

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
import threading
def print_server_url():
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        print(f"\nServer running! Access the web UI at: http://{ip}:8000/static/index.html\n")
    except Exception:
        print("\nServer running! Access the web UI at: http://localhost:8000/static/index.html\n")
threading.Thread(target=print_server_url, daemon=True).start()



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
    patient_info: str = Form(None)
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
        
        print(f"[DEBUG] Summary generated successfully! Length: {len(summary)} chars")
        
        # Return summary + symptom data
        return {
            'summary': summary,
            'symptoms_present': symptom_result['symptoms_present'],
            'unknown_mentions': symptom_result['unknown_mentions'],
            'symptom_count': symptom_result['symptom_count']
        }
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