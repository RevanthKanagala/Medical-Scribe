"""Summarizer using AssemblyAI LeMUR API.

Set your transcript file path directly in the script below (transcript_path variable).
After summarization, saves output to 'summarise.txt' in the same directory.
"""
import os
from pathlib import Path
import assemblyai as aai

# SET YOUR TRANSCRIPT FILE PATH HERE
transcript_path = r"D:\path\to\your_transcript.txt"  # <-- Change this to your transcript file path

api_key = os.getenv("ASSEMBLYAI_API_KEY")
if not api_key:
    raise SystemExit("ASSEMBLYAI_API_KEY not set. Use: setx ASSEMBLYAI_API_KEY \"<key>\" and reopen terminal.")

aai.settings.api_key = api_key

# Read the transcript
p = Path(transcript_path)
if not p.exists():
    raise FileNotFoundError(f'Transcript file not found: {p}')

text = p.read_text(encoding='utf-8')

print('Summarizing transcript with AssemblyAI...')

# Use AssemblyAI LeMUR for summarization
try:
    # Create a summarization request
    result = aai.Lemur().task(
        prompt=f"Please provide a concise clinical summary of the following patient-doctor conversation. Include: chief complaint, history, exam/observations (if present), assessment, and plan.\n\nConversation:\n{text}",
        final_model=aai.LemurModel.basic
    )
    
    summary = result.response
    
except Exception as e:
    print(f"Error during summarization: {e}")
    print("\nAlternative: Creating a simple summary...")
    # Fallback: create a simple summary
    lines = text.split('\n')
    summary = f"Summary of transcript ({len(lines)} lines, {len(text)} characters):\n\n"
    summary += text[:500] + "..." if len(text) > 500 else text

# Save summary to 'summarise.txt' in same directory
output_dir = p.parent
output_path = output_dir / 'summarise.txt'
output_path.write_text(summary, encoding='utf-8')

print('\n--- SUMMARY ---')
print(summary)
print(f'\nSummary saved to: {output_path}')
