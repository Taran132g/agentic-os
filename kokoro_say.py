#!/usr/bin/env python
"""CLI: kokoro_say.py "text" out.mp3 [voice] — local Kokoro TTS, zero API cost.

Runs under ~/agentic_os/.venv_tts (python3.12 + kokoro). Used by tiktok_stats.py
for the clips-agent voice line (2026-07-18: replaced ElevenLabs to stop quota burn).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

FFMPEG = "/opt/homebrew/bin/ffmpeg"


def main() -> None:
    text, out = sys.argv[1], Path(sys.argv[2])
    voice = sys.argv[3] if len(sys.argv) > 3 else "am_adam"
    pipe = KPipeline(lang_code="a")
    audio = np.concatenate([a for _, _, a in pipe(text, voice=voice, speed=1.0)])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    sf.write(wav, audio, 24000)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(wav),
                    "-c:a", "libmp3lame", "-q:a", "4", str(out)], check=True)
    wav.unlink()


main()
