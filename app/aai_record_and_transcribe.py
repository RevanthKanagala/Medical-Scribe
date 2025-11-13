"""Record from mic and transcribe using AssemblyAI (mirrors your provided script).

Usage:
  python -m app.aai_record_and_transcribe [--seconds N] [--input-index IDX] [--print-devices]
"""
import os
import argparse
import assemblyai as aai
from utils.record import record_wav, list_input_devices


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=60, help="Record duration")
    p.add_argument("--input-index", type=int, default=None, help="Mic device index (optional)")
    p.add_argument("--print-devices", action="store_true", help="List mic devices and exit")
    args = p.parse_args()

    if args.print_devices:
        devices = list_input_devices()
        for i, name in devices:
            print(f"{i}: {name}")
        return

    api_key = os.getenv("ASSEMBLYAI_API_KEY", "7b1e682337af4c67afe4e8edfb0985b3")
    if not api_key:
        raise SystemExit("ASSEMBLYAI_API_KEY not set. Use:  setx ASSEMBLYAI_API_KEY \"<key>\"  and reopen terminal.")

    aai.settings.api_key = api_key
    print("[aai] Recording mic…")
    wav_path = record_wav(duration_sec=args.seconds, input_index=args.input_index)

    # basic file transcription (upload handled by SDK)
    print("[aai] Transcribing…")
    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(wav_path)

    if transcript.error:
        print("[aai] FAILED:", transcript.error)
    else:
        print("\n--- TRANSCRIPT ---")
        print(transcript.text)


if __name__ == "__main__":
    main()
