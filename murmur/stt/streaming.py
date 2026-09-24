"""Phrase committing for streaming dictation.

While the key is held, audio accumulates. Every few hundred milliseconds
the app asks `find_commit` whether the uncommitted region contains a
finished phrase: speech followed by a pause. If so, that phrase is sent
to the engine in the background and the commit pointer advances. At
key-up only the short tail remains to be decoded, so the wait is a few
hundred milliseconds no matter how long the dictation ran.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

from murmur.config import SAMPLE_RATE

SpeechSpan = Tuple[int, int]      # start, end in samples


@dataclass(frozen=True)
class Commit:
    cut: int          # samples to take from the uncommitted region
    has_speech: bool  # False: the region was silence, just advance


def silero_spans(audio: np.ndarray, min_silence_ms: int = 300, pad_ms: int = 120) -> List[SpeechSpan]:
    """Speech spans via faster-whisper's bundled Silero VAD."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(min_silence_duration_ms=min_silence_ms, speech_pad_ms=pad_ms)
    return [(int(s["start"]), int(s["end"])) for s in get_speech_timestamps(audio, opts)]


def find_commit(
    since: np.ndarray,
    vad: Callable[[np.ndarray], Sequence[SpeechSpan]] = silero_spans,
    *,
    min_chunk_s: float = 2.0,
    pause_s: float = 0.45,
    silence_skip_s: float = 4.0,
    tail_pad_s: float = 0.15,
    sr: int = SAMPLE_RATE,
) -> Optional[Commit]:
    """Decide whether the uncommitted audio holds a finished phrase.

    Returns None to wait; a Commit with has_speech=False to drop leading
    silence; otherwise a Commit cutting after the last pause that follows
    at least `min_chunk_s` of audio.
    """
    n = int(since.size)
    if n < int(min_chunk_s * sr):
        return None
    spans = list(vad(since))
    if not spans:
        if n >= int(silence_skip_s * sr):
            return Commit(cut=n, has_speech=False)
        return None

    pause = int(pause_s * sr)
    pad = int(tail_pad_s * sr)

    # A pause after the last speech: everything so far is a finished phrase.
    last_end = spans[-1][1]
    if n - last_end >= pause:
        return Commit(cut=min(n, last_end + pad), has_speech=True)

    # Still talking: cut at the latest earlier gap that leaves a long-enough chunk.
    for i in range(len(spans) - 2, -1, -1):
        gap = spans[i + 1][0] - spans[i][1]
        if gap >= pause and spans[i][1] >= int(min_chunk_s * sr):
            return Commit(cut=min(n, spans[i][1] + pad), has_speech=True)
    return None
