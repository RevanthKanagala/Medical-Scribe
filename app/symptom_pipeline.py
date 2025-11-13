"""
AIMS Symptom Extraction and Normalization Pipeline
Prevents hallucinations by using only validated symptoms from catalog.

Flow:
1. Extract potential symptoms from transcript (NER/keywords)
2. Normalize to official symptom catalog
3. Split into known vs unknown
4. Output only known symptoms in JSON
5. Flag unknown mentions for human review
6. Humans approve/reject unknowns
7. Approved unknowns added to catalog
"""
import csv
import json
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass, asdict

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('AIMS_SymptomPipeline')

# Paths for logging
LOGS_DIR = Path(__file__).parent.parent / 'logs'
LOGS_DIR.mkdir(exist_ok=True)
UNKNOWN_SYMPTOMS_LOG = LOGS_DIR / 'unknown_symptoms.csv'


@dataclass
class Symptom:
    """Validated symptom from catalog."""
    code: str
    name: str
    aliases: List[str]
    category: str


@dataclass
class ExtractionResult:
    """Result of symptom extraction pipeline."""
    symptoms_present: List[Dict[str, str]]  # Known symptoms
    unknown_mentions: List[str]  # Needs human review
    raw_transcript: str


class SymptomCatalog:
    """Manages the official symptom catalog."""
    
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.symptoms: Dict[str, Symptom] = {}
        self.aliases_map: Dict[str, str] = {}  # alias -> symptom_code
        self.load()
    
    def load(self):
        """Load symptoms from disease-symptom dataset CSV (header row contains all symptoms)."""
        self.symptoms.clear()
        self.aliases_map.clear()
        
        if not self.csv_path.exists():
            logger.warning(f"Symptom catalog not found: {self.csv_path}")
            return
        
        # Read first row (header) which contains all symptom names
        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)  # First row: diseases, symptom1, symptom2, ...
        
        # Extract symptoms (skip first column "diseases")
        symptom_names = header[1:]
        
        # Auto-categorize symptoms
        def categorize(name):
            nl = name.lower()
            if any(w in nl for w in ['heart', 'chest', 'cardiac', 'palpitation']): return 'cardiovascular'
            if any(w in nl for w in ['breath', 'cough', 'wheez', 'throat', 'nose', 'sinus']): return 'respiratory'
            if any(w in nl for w in ['head', 'dizz', 'seizure', 'memory', 'confusion']): return 'neurological'
            if any(w in nl for w in ['stomach', 'abdominal', 'bowel', 'diarrhea', 'vomit', 'nausea']): return 'gastrointestinal'
            if any(w in nl for w in ['joint', 'muscle', 'back', 'neck', 'leg', 'arm', 'knee', 'hip']): return 'musculoskeletal'
            if any(w in nl for w in ['skin', 'rash', 'itch', 'lesion']): return 'dermatological'
            if any(w in nl for w in ['anxiety', 'depression', 'psycho', 'emotion']): return 'psychological'
            if any(w in nl for w in ['urin', 'bladder', 'kidney']): return 'urological'
            if any(w in nl for w in ['eye', 'vision']): return 'visual'
            if any(w in nl for w in ['ear', 'hearing']): return 'ENT'
            if any(w in nl for w in ['menstrual', 'pregnancy', 'vaginal']): return 'reproductive'
            return 'general'
        
        # Create symptom catalog entries
        for idx, name in enumerate(symptom_names, start=1):
            code = f"S{idx:05d}"
            category = categorize(name)
            symptom = Symptom(code, name.strip(), [name.strip().lower()], category)
            self.symptoms[code] = symptom
            self.aliases_map[name.strip().lower()] = code
        
        logger.info(f"Loaded {len(self.symptoms)} symptoms with {len(self.aliases_map)} mappings from catalog")
    
    def find_symptom_by_text(self, text: str) -> Tuple[str, Symptom]:
        """Find symptom by matching text. Returns (matched_text, Symptom) or (None, None)."""
        text_lower = text.lower().strip()
        code = self.aliases_map.get(text_lower)
        if code:
            return text, self.symptoms[code]
        return None, None
    
    def add_symptom(self, name: str, category: str = 'general', aliases: List[str] = None):
        """Add new symptom to catalog (used when human approves unknown).
        Note: For the large dataset, we only add to memory, not the CSV (too large to modify).
        """
        # Generate new code
        existing_codes = [int(s.code[1:]) for s in self.symptoms.values()]
        new_code_num = max(existing_codes) + 1 if existing_codes else 1
        code = f"S{new_code_num:05d}"
        
        aliases = aliases or []
        symptom = Symptom(code, name, aliases, category)
        self.symptoms[code] = symptom
        
        # Update aliases map
        self.aliases_map[name.lower()] = code
        for alias in aliases:
            if alias:
                self.aliases_map[alias.lower()] = code
        
        # Note: Not appending to the large CSV file (190MB) to avoid performance issues
        # Approved symptoms are only stored in memory for this session
        logger.info(f"Added symptom {code}: {name} (in-memory only)")
        return code


class SymptomExtractor:
    """Extracts potential symptoms from transcript."""
    
    def __init__(self, catalog: SymptomCatalog):
        self.catalog = catalog
    
    def extract_phrases(self, text: str) -> List[str]:
        """Extract potential symptom phrases - aggressive matching for all catalog symptoms."""
        text_lower = text.lower()
        candidates = []
        
        # Strategy 1: Check ALL symptoms in catalog against the transcript
        # This ensures we don't miss any symptoms that appear in the text
        for symptom_text in self.catalog.aliases_map.keys():
            if symptom_text in text_lower:
                candidates.append(symptom_text)
        
        # Strategy 2: Pattern-based extraction for additional context
        patterns = [
            r'(?:have|has|had|experiencing|feeling|feel|feels|complains of|reports|presenting with)\s+(?:a\s+)?([a-z\s]{3,40})',
            r'(?:pain in|ache in|discomfort in|tightness in|pressure in)\s+(?:my\s+|the\s+)?([a-z\s]{3,30})',
            r'my\s+([a-z\s]{3,25})\s+(?:hurts|aches|is sore|feels)',
            r'(?:severe|sharp|dull|mild|chronic)\s+([a-z\s]{3,25})',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                m = match.strip()
                # Check if this extracted phrase matches any symptom
                if m in self.catalog.aliases_map:
                    candidates.append(m)
                # Also check partial matches (e.g., "chest pain" from "sharp chest pain")
                for symptom_text in self.catalog.aliases_map.keys():
                    if m in symptom_text or symptom_text in m:
                        candidates.append(symptom_text)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen and len(c) > 2:
                seen.add(c)
                unique_candidates.append(c)
        
        return unique_candidates


class SymptomNormalizer:
    """Normalizes extracted phrases to catalog or marks as unknown."""
    
    def __init__(self, catalog: SymptomCatalog, extractor: SymptomExtractor):
        self.catalog = catalog
        self.extractor = extractor
    
    def process_transcript(self, transcript: str) -> ExtractionResult:
        """
        Complete pipeline: Extract → Normalize → Split known/unknown
        Logs patient transcript, matched symptoms, and unknown mentions.
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Log original transcript
        logger.info("="*80)
        logger.info(f"[PATIENT TRANSCRIPT] Timestamp: {timestamp}")
        logger.info(f"[PATIENT TRANSCRIPT] Full text: {transcript}")
        logger.info("="*80)
        
        # Step 1: Extract potential symptoms
        raw_phrases = self.extractor.extract_phrases(transcript)
        logger.info(f"[EXTRACTION] Found {len(raw_phrases)} potential symptom phrases")
        logger.info(f"[EXTRACTION] Raw phrases: {raw_phrases}")
        
        # Step 2: Normalize and split
        symptoms_present = []
        unknown_mentions = []
        
        for phrase in raw_phrases:
            matched_text, symptom = self.catalog.find_symptom_by_text(phrase)
            
            if symptom:
                # ✅ Known symptom - MATCHED with CSV
                symptoms_present.append({
                    'code': symptom.code,
                    'name': symptom.name,
                    'matched_text': matched_text,
                    'category': symptom.category
                })
                logger.info(f"[MATCHED] ✅ '{phrase}' → {symptom.code}: {symptom.name} ({symptom.category})")
            else:
                # ❌ Unknown mention - NOT in CSV
                unknown_mentions.append(phrase)
                logger.warning(f"[UNKNOWN] ❌ '{phrase}' - NOT FOUND in symptom catalog")
                
                # Log unknown symptom to CSV for review
                self._log_unknown_symptom(phrase, transcript, timestamp)
        
        # Remove duplicate symptoms by code
        seen_codes = set()
        unique_symptoms = []
        for s in symptoms_present:
            if s['code'] not in seen_codes:
                seen_codes.add(s['code'])
                unique_symptoms.append(s)
        
        # Log summary
        logger.info("="*80)
        logger.info(f"[SUMMARY] Total matched symptoms: {len(unique_symptoms)}")
        logger.info(f"[SUMMARY] Total unknown mentions: {len(set(unknown_mentions))}")
        if unique_symptoms:
            logger.info("[SUMMARY] Matched symptoms list:")
            for s in unique_symptoms:
                logger.info(f"  - {s['code']}: {s['name']} ({s['category']})")
        if unknown_mentions:
            logger.info("[SUMMARY] Unknown mentions list:")
            for u in set(unknown_mentions):
                logger.info(f"  - {u}")
        logger.info("="*80)
        
        return ExtractionResult(
            symptoms_present=unique_symptoms,
            unknown_mentions=list(set(unknown_mentions)),  # Dedupe unknowns
            raw_transcript=transcript
        )
    
    def _log_unknown_symptom(self, symptom_text: str, transcript: str, timestamp: str):
        """Log unknown symptom to CSV file for later review."""
        try:
            # Check if CSV exists, create with header if not
            file_exists = UNKNOWN_SYMPTOMS_LOG.exists()
            
            with open(UNKNOWN_SYMPTOMS_LOG, 'a', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                
                # Write header if new file
                if not file_exists:
                    writer.writerow(['Timestamp', 'Unknown_Symptom', 'Context_Transcript', 'Status'])
                
                # Write unknown symptom entry
                writer.writerow([timestamp, symptom_text, transcript[:200], 'Pending Review'])
                
        except Exception as e:
            logger.error(f"Failed to log unknown symptom to CSV: {e}")


# Global instances
SYMPTOM_CATALOG_PATH = Path(__file__).parent.parent / 'data' / 'Final_Augmented_dataset_Diseases_and_Symptoms.csv'
symptom_catalog = SymptomCatalog(SYMPTOM_CATALOG_PATH)
symptom_extractor = SymptomExtractor(symptom_catalog)
symptom_normalizer = SymptomNormalizer(symptom_catalog, symptom_extractor)


def extract_symptoms_from_transcript(transcript: str) -> Dict:
    """Main entry point for symptom extraction pipeline."""
    result = symptom_normalizer.process_transcript(transcript)
    return {
        'symptoms_present': result.symptoms_present,
        'unknown_mentions': result.unknown_mentions,
        'symptom_count': len(result.symptoms_present),
        'unknown_count': len(result.unknown_mentions)
    }


def approve_unknown_symptom(mention: str, category: str = 'general', aliases: List[str] = None) -> str:
    """Human approves an unknown mention and adds it to catalog."""
    code = symptom_catalog.add_symptom(mention, category, aliases or [])
    # Reload catalog to pick up new mappings
    symptom_catalog.load()
    return code
