"""Simple single-file transcription using AssemblyAI SDK.

Set your audio file path directly in the script below (audio_path variable).
After transcription, saves output to a text file named with first 4 letters of audio file + .txt
"""
import os
import assemblyai as aai

# SET YOUR AUDIO FILE PATH HERE
audio_path = r"D:\AIDI\Medical Scribe\recorder\Lone Wolf - Motivational Video For All Those Fighting Battles Alone.mp3"  # <-- Change this to your audio file path

api_key = os.getenv('ASSEMBLYAI_API_KEY')
if not api_key:
    raise SystemExit('ASSEMBLYAI_API_KEY not set. Use: setx ASSEMBLYAI_API_KEY "<key>" and reopen terminal.')

aai.settings.api_key = api_key
transcriber = aai.Transcriber()
transcript = transcriber.transcribe(audio_path)

if getattr(transcript, 'error', None):
    print('Transcription failed:', transcript.error)
else:
    print('\n--- TRANSCRIPT ---')
    print(transcript.text)
    
    # Save transcript to file with first 4 letters of audio filename + .txt
    base_name = os.path.basename(audio_path)
    name_prefix = base_name[:4]
    output_dir = os.path.dirname(audio_path) or '.'
    output_path = os.path.join(output_dir, f"{name_prefix}.txt")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(transcript.text)
    
    print(f'\nTranscript saved to: {output_path}')
