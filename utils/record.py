"""Small recording helper used by the AssemblyAI record+transcribe script.

Provides:
 - list_input_devices() -> list of (index, name)
 - record_wav(duration_sec=60, input_index=None, out_dir='uploads') -> path to WAV file

Requires sounddevice and soundfile packages.
"""
from pathlib import Path
import os
import time


def list_input_devices():
    try:
        import sounddevice as sd
    except Exception:
        raise RuntimeError('sounddevice is required for listing devices')

    devices = sd.query_devices()
    input_devices = []
    for i, d in enumerate(devices):
        # device info may contain 'max_input_channels'
        if isinstance(d, dict):
            max_in = d.get('max_input_channels', 0)
            name = d.get('name')
        else:
            max_in = getattr(d, 'max_input_channels', 0)
            name = getattr(d, 'name', str(d))
        if max_in and max_in > 0:
            input_devices.append((i, name))
    return input_devices


def record_wav(duration_sec=60, input_index=None, out_dir='uploads'):
    try:
        import sounddevice as sd
        import soundfile as sf
    except Exception:
        raise RuntimeError('sounddevice and soundfile are required for recording')

    out_dir = Path(os.getenv('UPLOAD_DIR', out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    fs = 16000
    channels = 1
    print(f'Recording for {duration_sec}s (samplerate={fs})')
    if input_index is not None:
        sd.default.device = input_index

    recording = sd.rec(int(duration_sec * fs), samplerate=fs, channels=channels)
    try:
        sd.wait()
    except KeyboardInterrupt:
        sd.stop()

    ts = int(time.time())
    out_path = out_dir / f'recording_{ts}.wav'
    sf.write(str(out_path), recording, fs)
    return str(out_path)
