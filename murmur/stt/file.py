from __future__ import annotations

from pathlib import Path

import numpy as np

from murmur.config import SAMPLE_RATE

# Anything PyAV/ffmpeg can open works; these are just the common ones for the
# error message and the file dialog filter.
AUDIO_EXTENSIONS = (
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".mp4", ".mov", ".webm", ".mkv", ".caf", ".amr", ".3gp",
)


def decode_file(path: str | Path) -> np.ndarray:
    """Decode any audio/video file to the float32 mono 16 kHz buffer the
    engine expects. Uses faster-whisper's bundled PyAV decoder, so no ffmpeg
    binary is required."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"audio file not found: {path}")
    from faster_whisper.audio import decode_audio

    audio = decode_audio(str(path), sampling_rate=SAMPLE_RATE, split_stereo=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=0) if audio.shape[0] < audio.shape[-1] else audio.mean(axis=-1)
    return np.ascontiguousarray(audio)


def duration_s(audio: np.ndarray) -> float:
    return float(audio.size) / SAMPLE_RATE


def fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
