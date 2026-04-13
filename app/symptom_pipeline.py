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
from collections import defaultdict

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

TOKEN_NORMALIZATIONS = {
    'abdomen': 'abdominal',
    'stomach': 'abdominal',
    'belly': 'abdominal',
    'tummy': 'abdominal',
    'lumbar': 'back',
    'pains': 'pain',
    'painful': 'pain',
    'ache': 'pain',
    'aches': 'pain',
    'aching': 'pain',
    'soreness': 'pain',
    'sore': 'pain',
    'breathing': 'breath',
    'breathe': 'breath',
    'breathlessness': 'breath',
    'constipated': 'constipation',
    'pooping': 'stool',
    'toilet': 'stool',
}

OPTIONAL_MODIFIERS = {
    'sharp', 'dull', 'burning', 'upper', 'lower', 'low', 'left', 'right',
    'severe', 'mild', 'chronic', 'acute', 'bad', 'really', 'generalized'
}

CONTEXTUAL_SYMPTOM_RULES = [
    (r'(?:trouble|struggling|difficulty|hard)\s+(?:going|to go|passing)\s+(?:to\s+the\s+)?(?:toilet|stool|bowel|bowels|bowel movement)', 'constipation'),
    (r'(?:unable|cannot|can\'t)\s+to\s+(?:go|pass)\s+(?:to\s+the\s+)?(?:toilet|stool|bowel|bowels)', 'constipation'),
    (r'(?:stomach|belly|abdomen|abdominal)\s+(?:pain|pains|ache|aches)', 'abdominal pain'),
    (r'(?:lower\s+back|back)\s+(?:pain|ache|aches)', 'back pain'),
]


def _normalize_whitespace(text: str) -> str:
    return ' '.join(text.split())


def normalize_phrase(text: str) -> str:
    cleaned = re.sub(r'[^a-z0-9\s]', ' ', (text or '').lower())
    return _normalize_whitespace(cleaned)


def normalize_tokens(text: str, strip_modifiers: bool = False) -> List[str]:
    tokens = []
    for token in normalize_phrase(text).split():
        normalized = TOKEN_NORMALIZATIONS.get(token, token)
        if normalized.endswith('s') and len(normalized) > 4 and normalized not in {'nauseous'}:
            normalized = normalized[:-1]
        if strip_modifiers and normalized in OPTIONAL_MODIFIERS:
            continue
        tokens.append(normalized)
    return tokens


def build_alias_variants(text: str) -> Set[str]:
    variants = set()
    exact = normalize_phrase(text)
    if exact:
        variants.add(exact)

    normalized_tokens = normalize_tokens(text, strip_modifiers=False)
    if normalized_tokens:
        variants.add(' '.join(normalized_tokens))

    reduced_tokens = normalize_tokens(text, strip_modifiers=True)
    if len(reduced_tokens) >= 2 or (len(reduced_tokens) == 1 and reduced_tokens[0] not in {'pain', 'problem'}):
        variants.add(' '.join(reduced_tokens))

    return {variant for variant in variants if variant}


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
        self.aliases_map: Dict[str, Set[str]] = defaultdict(set)  # alias -> symptom_codes
        self.load()
    
    def load(self):
        """Load symptoms from either the large dataset or the structured symptom catalog."""
        self.symptoms.clear()
        self.aliases_map.clear()

        candidate_paths = [
            self.csv_path,
            Path(__file__).parent.parent / 'data' / 'symptoms_catalog.csv',
            Path(__file__).parent.parent / 'symptoms_catalog.csv',
        ]
        existing_path = next((path for path in candidate_paths if path.exists()), None)

        if not existing_path:
            logger.warning(f"Symptom catalog not found: {self.csv_path}")
            return

        self.csv_path = existing_path

        with open(existing_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)

            normalized_header = [column.strip().lower() for column in header[:4]]
            if normalized_header == ['code', 'name', 'aliases', 'category']:
                self._load_structured_catalog(reader)
                logger.info(
                    f"Loaded {len(self.symptoms)} symptoms with {len(self.aliases_map)} mappings from structured catalog {existing_path.name}"
                )
                return

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
            aliases = sorted(build_alias_variants(name.strip()))
            symptom = Symptom(code, name.strip(), aliases, category)
            self._register_symptom(symptom)
        
        logger.info(f"Loaded {len(self.symptoms)} symptoms with {len(self.aliases_map)} mappings from catalog {existing_path.name}")

    def _load_structured_catalog(self, reader):
        for row in reader:
            if len(row) < 4:
                continue
            code = row[0].strip()
            name = row[1].strip()
            aliases_raw = row[2].strip()
            category = row[3].strip() or 'general'
            if not code or not name:
                continue

            aliases = []
            for alias in [name, *aliases_raw.split('|')]:
                aliases.extend(build_alias_variants(alias))

            symptom = Symptom(code, name, sorted(set(aliases)), category)
            self._register_symptom(symptom)

    def _register_symptom(self, symptom: Symptom):
        self.symptoms[symptom.code] = symptom
        for alias in symptom.aliases:
            self.aliases_map[alias].add(symptom.code)
    
    def find_symptom_by_text(self, text: str) -> Tuple[str, Symptom]:
        """Find symptom by matching text. Returns (matched_text, Symptom) or (None, None)."""
        matches = self.find_symptoms_by_text(text)
        if matches:
            return matches[0]
        return None, None

    def find_symptoms_by_text(self, text: str) -> List[Tuple[str, Symptom]]:
        variants = [variant for variant in build_alias_variants(text) if variant]
        seen_codes = set()
        matches: List[Tuple[str, Symptom]] = []
        for variant in variants:
            for code in sorted(self.aliases_map.get(variant, set())):
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                matches.append((variant, self.symptoms[code]))
        return matches
    
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
        """Extract potential symptom phrases using exact, normalized, and contextual n-gram matching."""
        text_lower = normalize_phrase(text)
        candidates = []

        padded_text = f" {text_lower} "
        for symptom_text in self.catalog.aliases_map.keys():
            if len(symptom_text) < 3:
                continue
            if f" {symptom_text} " in padded_text:
                candidates.append(symptom_text)

        tokens = text_lower.split()
        for start in range(len(tokens)):
            for size in range(1, min(7, len(tokens) - start + 1)):
                candidate = ' '.join(tokens[start:start + size])
                for variant in build_alias_variants(candidate):
                    if variant in self.catalog.aliases_map:
                        candidates.append(variant)
        
        patterns = [
            r'(?:have|has|had|experiencing|feeling|feel|feels|complains of|reports|presenting with)\s+(?:a\s+)?([a-z\s]{3,40})',
            r'(?:pain in|ache in|discomfort in|tightness in|pressure in)\s+(?:my\s+|the\s+)?([a-z\s]{3,30})',
            r'my\s+([a-z\s]{3,25})\s+(?:hurts|aches|is sore|feels)',
            r'(?:severe|sharp|dull|mild|chronic|burning|upper|lower|low)\s+([a-z\s]{3,25})',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                m = normalize_phrase(match.strip())
                for variant in build_alias_variants(m):
                    if variant in self.catalog.aliases_map:
                        candidates.append(variant)

        for pattern, canonical_symptom in CONTEXTUAL_SYMPTOM_RULES:
            if re.search(pattern, text_lower):
                for variant in build_alias_variants(canonical_symptom):
                    if variant in self.catalog.aliases_map:
                        candidates.append(variant)
        
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
            matches = self.catalog.find_symptoms_by_text(phrase)

            if matches:
                for matched_text, symptom in matches:
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
