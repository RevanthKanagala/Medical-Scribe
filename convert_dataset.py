"""
Extract all unique symptoms from Final_Augmented_dataset_Diseases_and_Symptoms.csv
and convert to symptoms_catalog.csv format with codes, names, aliases, and categories.
"""
import csv
from pathlib import Path
import sqlite3
from databases import Database

# Paths
large_dataset = Path("data/Final_Augmented_dataset_Diseases_and_Symptoms.csv")
output_catalog = Path("data/symptoms_catalog.csv")

print(f"Reading symptoms from: {large_dataset}")
print(f"Output will be saved to: {output_catalog}")

# Read the first row (header) which contains all symptom names
with open(large_dataset, 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    header = next(reader)  # First row: diseases, symptom1, symptom2, ...

# Extract symptoms (skip first column which is "diseases")
symptoms = header[1:]
print(f"\nFound {len(symptoms)} unique symptoms in dataset")

# Category mapping based on symptom keywords
def categorize_symptom(symptom_name):
    name_lower = symptom_name.lower()
    
    # Cardiovascular
    if any(word in name_lower for word in ['heart', 'chest', 'cardiac', 'palpitation', 'circulation', 'blood pressure']):
        return 'cardiovascular'
    
    # Respiratory
    if any(word in name_lower for word in ['breath', 'lung', 'cough', 'wheez', 'respiratory', 'throat', 'sinus', 'nose', 'nasal', 'sputum']):
        return 'respiratory'
    
    # Neurological
    if any(word in name_lower for word in ['head', 'dizz', 'seizure', 'memory', 'confusion', 'nerve', 'paralysis', 'neurological', 'brain', 'conscious', 'cognitive']):
        return 'neurological'
    
    # Gastrointestinal
    if any(word in name_lower for word in ['stomach', 'abdominal', 'bowel', 'diarrhea', 'vomit', 'nausea', 'digest', 'stool', 'constipation', 'intestin', 'rectal', 'anus']):
        return 'gastrointestinal'
    
    # Musculoskeletal
    if any(word in name_lower for word in ['joint', 'bone', 'muscle', 'back', 'neck', 'shoulder', 'leg', 'arm', 'knee', 'hip', 'elbow', 'ankle', 'wrist', 'foot', 'toe', 'hand', 'finger']):
        return 'musculoskeletal'
    
    # Dermatological
    if any(word in name_lower for word in ['skin', 'rash', 'itch', 'lesion', 'wound', 'blister', 'mole', 'wart', 'scalp', 'hair', 'nail']):
        return 'dermatological'
    
    # Psychological
    if any(word in name_lower for word in ['anxiety', 'depression', 'psycho', 'emotion', 'mood', 'stress', 'fear', 'phobia', 'panic', 'behavior', 'sleep', 'insomnia']):
        return 'psychological'
    
    # Urological
    if any(word in name_lower for word in ['urin', 'bladder', 'kidney', 'prostate', 'renal']):
        return 'urological'
    
    # Visual
    if any(word in name_lower for word in ['eye', 'vision', 'blind', 'sight', 'eyelid', 'pupil']):
        return 'visual'
    
    # ENT (Ear, Nose, Throat)
    if any(word in name_lower for word in ['ear', 'hearing', 'tinnitus', 'deaf']):
        return 'ENT'
    
    # Reproductive
    if any(word in name_lower for word in ['menstrual', 'pregnancy', 'vaginal', 'uterine', 'sexual', 'breast', 'testicle', 'penis', 'scrotum', 'vulva', 'ovarian']):
        return 'reproductive'
    
    # General (catch-all)
    return 'general'

# Create symptom catalog entries
catalog_entries = []
for idx, symptom in enumerate(symptoms, start=1):
    code = f"S{idx:05d}"  # S00001, S00002, etc.
    name = symptom.strip()
    category = categorize_symptom(name)
    
    # For aliases, just use the name itself (no variants yet)
    # Users can add aliases later through the approval system
    aliases = name
    
    catalog_entries.append({
        'code': code,
        'name': name,
        'aliases': aliases,
        'category': category
    })

# Write to catalog CSV
print(f"\nWriting {len(catalog_entries)} symptoms to catalog...")
with open(output_catalog, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['code', 'name', 'aliases', 'category'])
    writer.writeheader()
    writer.writerows(catalog_entries)

print(f"✅ Catalog created successfully!")
print(f"   Total symptoms: {len(catalog_entries)}")
print(f"   Output file: {output_catalog}")

# Show category breakdown
from collections import Counter
category_counts = Counter(entry['category'] for entry in catalog_entries)
print("\n📊 Symptoms by category:")
for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
    print(f"   {category}: {count}")

print("\n✅ First 10 symptoms:")
for entry in catalog_entries[:10]:
    print(f"   {entry['code']}: {entry['name']} ({entry['category']})")

# Connect to the database
DATABASE_URL = "sqlite:///medical_scribe.db"
database = Database(DATABASE_URL)

async def setup_database():
    await database.connect()
    query = """
    CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT NOT NULL,
        designation TEXT NOT NULL,
        patient_type TEXT
    )
    """
    await database.execute(query)

async def add_doctor(name, department, designation, patient_type):
    query = """
    INSERT INTO doctors (name, department, designation, patient_type)
    VALUES (:name, :department, :designation, :patient_type)
    """
    values = {"name": name, "department": department, "designation": designation, "patient_type": patient_type}
    await database.execute(query, values)

async def get_doctors():
    query = "SELECT * FROM doctors"
    return await database.fetch_all(query)

# Initialize the database
await setup_database()

# Insert a record
await add_doctor("Dr. Smith", "Cardiology", "Consultant", "Outpatient")

# Query records
doctors = await get_doctors()
for doctor in doctors:
    print(doctor)

# Commit and close
await database.close()
