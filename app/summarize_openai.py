"""Summarizer that reads a transcript file and writes a summary using OpenAI.

Set your transcript file path directly in the script below (transcript_path variable).
After summarization, saves output to 'summarise.txt' in the same directory.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# SET YOUR TRANSCRIPT FILE PATH HERE
transcript_path = r"D:\AIDI\Medical Scribe\recorder\Lone.txt"  # <-- Change this to your transcript file path

def _get_client():
    try:
        import openai as o
    except Exception:
        raise RuntimeError('openai package not installed. Install with: pip install openai')

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError('OPENAI_API_KEY not set in environment or .env')

    if hasattr(o, 'OpenAI'):
        return o.OpenAI(api_key=api_key), 'modern'
    o.api_key = api_key
    return o, 'legacy'


def summarize_text(text: str, max_tokens: int = 512) -> str:
    client, mode = _get_client()
    prompt = (
        'Please provide a concise clinical summary of the following patient-doctor conversation. '
        'Include: chief complaint, history, exam/observations (if present), assessment, and plan.\n\nConversation:\n' + text
    )

    if mode == 'modern':
        resp = client.chat.completions.create(
            model='gpt-3.5-turbo',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        return getattr(choice.message, 'content', None) or (choice['message']['content'] if isinstance(choice, dict) else '')
    else:
        import openai as _openai
        resp = _openai.ChatCompletion.create(
            model='gpt-3.5-turbo',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=max_tokens,
        )
        return resp['choices'][0]['message']['content']


# Main execution
p = Path(transcript_path)
if not p.exists():
    raise FileNotFoundError(f'Transcript file not found: {p}')

text = p.read_text(encoding='utf-8')
print('Summarizing transcript...')
summary = summarize_text(text)

# Save summary to 'summarise.txt' in same directory
output_dir = p.parent
output_path = output_dir / 'summarise.txt'
output_path.write_text(summary, encoding='utf-8')

print('\n--- SUMMARY ---')
print(summary)
print(f'\nSummary saved to: {output_path}')
