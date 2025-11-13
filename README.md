# Medical Scribe — Recording, Transcription, and Summarization

This project contains a simple prototype for recording audio, transcribing using OpenAI audio models (Whisper), and summarizing transcripts with OpenAI chat models.

Quick start (Windows):

1. Copy `.env` and set your OpenAI key:

   - Open `.env` and replace OPENAI_API_KEY value.

2. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

Or run the provided script:

```
setup_venv.bat
```

3. Start the API server (from project root):

```
start_server.bat
```

4. Open http://127.0.0.1:8000 in Chrome to use the web recorder and uploader.

CLI utilities:

- `recorder/cli_recorder.py` — interactive start/stop recording and save WAV; will optionally transcribe.
- `transcribe_file.py <path>` — transcribe an existing audio file and write a .txt transcript.
- `summarize.py <transcript.txt>` — produce a summary and write <name>_summary.txt and append summary to original.

Notes:

- The code uses environment variable `OPENAI_API_KEY`. Keep your key secret.
- This is a prototype. For production, add authentication, error handling, and secure storage.

AssemblyAI (optional)
---------------------
If you prefer AssemblyAI for transcription, you can use the AssemblyAI SDK. Add the key to your `.env`:

```
ASSEMBLYAI_API_KEY=your_assemblyai_key_here
```

Install the SDK:

```
.\.venv\Scripts\pip install assemblyai
```

Then run the AssemblyAI CLI wrapper:

```
python -m app.assemblyai_transcribe path\to\audio.wav
```

This will write a `path.assemblyai.txt` file next to the audio with the transcript.

OpenAI (Whisper + Summarize) Server
-----------------------------------
If you prefer to use OpenAI's Whisper and chat models, there's a small FastAPI server:

 - `app/openai_server.py` exposes `/upload` (audio file upload -> Whisper transcription) and `/summarize` (text -> summary).

Set your OpenAI key in `.env`:

```
OPENAI_API_KEY=your_openai_key_here
```

Install dependencies and start the server:

```cmd
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.openai_server:app --reload --host 127.0.0.1 --port 8000
```

Or use the helper script:

```cmd
start_server.bat
```

You can then open the static UI at http://127.0.0.1:8000 (if `web/static/index.html` is present) to record/upload and summarize.

CLI Summarizer (OpenAI)
------------------------
There's also a CLI summarizer that reads a transcript file and writes a summary:

```
python -m app.summarize_openai path\to\transcript.txt
```

This will create `path_summary.txt` and append the summary to the original transcript file.
