from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Iterator, Optional

import numpy as np

from murmur.config import MODELS_DIR, Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Segment:
    start: float   # seconds
    end: float     # seconds
    text: str


class WhisperEngine:
    """Resident faster-whisper model. Loaded once, reused per utterance."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = Lock()

    def load(self) -> None:
        """Eagerly load the model. Safe to call from a worker thread."""
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        t0 = time.perf_counter()
        with self._lock:
            if self._model is not None:
                return
            log.info(
                "loading faster-whisper model=%s device=%s compute=%s",
                self.settings.model, self.settings.device, self.settings.compute_type,
            )
            self._model = WhisperModel(
                self.settings.model,
                device=self.settings.device,
                compute_type=self.settings.compute_type,
                download_root=str(MODELS_DIR),
            )
        log.info("model loaded in %.2fs", time.perf_counter() - t0)

    def is_ready(self) -> bool:
        return self._model is not None

    def _build_initial_prompt(self) -> str | None:
        # Whisper uses initial_prompt as a soft bias — short, comma-separated
        # phrases are most effective. Cap at ~200 chars to stay well under the
        # 224-token prompt budget.
        terms = [t.strip() for t in (self.settings.dictionary or []) if t.strip()]
        if not terms:
            return None
        joined = ", ".join(terms)
        if len(joined) > 200:
            joined = joined[:200]
        return f"Glossary: {joined}."

    def _prepare(self, audio: np.ndarray) -> np.ndarray:
        if self._model is None:
            self.load()
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32, copy=False)
        if audio.ndim > 1:
            audio = audio.flatten()
        return audio

    def iter_segments(self, audio: np.ndarray) -> Iterator[Segment]:
        """Stream timestamped segments for a float32 mono 16kHz buffer.

        faster-whisper decodes lazily, so callers iterating this see segments
        as they are produced — useful for progress on long files.
        """
        audio = self._prepare(audio)
        assert self._model is not None
        segments, _info = self._model.transcribe(
            audio,
            language="en",
            beam_size=self.settings.beam_size,
            vad_filter=self.settings.vad_filter,
            condition_on_previous_text=False,
            initial_prompt=self._build_initial_prompt(),
        )
        for s in segments:
            text = s.text.strip()
            if text:
                yield Segment(start=float(s.start), end=float(s.end), text=text)

    def transcribe(
        self,
        audio: np.ndarray,
        on_segment: Optional[Callable[[Segment], None]] = None,
    ) -> str:
        """Transcribe a float32 mono 16kHz buffer. Blocking.

        `on_segment` is called for each segment as it is decoded (progress).
        """
        t0 = time.perf_counter()
        parts: list[str] = []
        for seg in self.iter_segments(audio):
            parts.append(seg.text)
            if on_segment is not None:
                on_segment(seg)
        text = " ".join(parts).strip()
        log.info("transcribed %.2fs audio in %.2fs -> %d chars",
                 audio.size / 16000, time.perf_counter() - t0, len(text))
        return text
