"""NVIDIA Parakeet TDT 0.6B v3 through onnx-asr (int8, CPU).

Measured on a Ryzen 3700X: ~100 ms fixed plus ~55 ms per second of speech,
so a 5 s utterance decodes in ~380 ms against ~3 s for faster-whisper
small.en, with a lower word error rate. The model (~600 MB) downloads
from Hugging Face on first use into the standard hub cache.
"""
from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Callable, Iterator, Optional

import numpy as np

from murmur.config import SAMPLE_RATE, Settings
from murmur.stt.engine import Segment, _cpu_threads

log = logging.getLogger(__name__)

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
LONG_FORM_S = 25.0      # above this, chunk with VAD so memory and latency stay flat


class ParakeetEngine:
    name = "parakeet"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._vad = None
        self._lock = Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        import onnx_asr
        import onnxruntime as ort

        t0 = time.perf_counter()
        with self._lock:
            if self._model is not None:
                return
            so = ort.SessionOptions()
            so.intra_op_num_threads = _cpu_threads()
            so.inter_op_num_threads = 1
            log.info("loading %s int8 (threads=%d)", MODEL_NAME, so.intra_op_num_threads)
            self._model = onnx_asr.load_model(
                MODEL_NAME, quantization="int8", sess_options=so, providers=["CPUExecutionProvider"],
            )
            try:
                self._vad = onnx_asr.load_vad("silero")
            except Exception:
                log.exception("silero VAD unavailable; long files will be decoded whole")
        log.info("model loaded in %.2fs", time.perf_counter() - t0)

    def warmup(self) -> None:
        if self._model is None:
            return
        t0 = time.perf_counter()
        try:
            with self._lock:
                self._model.recognize(np.zeros(SAMPLE_RATE, dtype=np.float32), sample_rate=SAMPLE_RATE)
        except Exception:
            log.exception("warmup failed (harmless)")
        log.info("warmup in %.2fs", time.perf_counter() - t0)

    def is_ready(self) -> bool:
        return self._model is not None

    def _prepare(self, audio: np.ndarray) -> np.ndarray:
        if self._model is None:
            self.load()
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32, copy=False)
        if audio.ndim > 1:
            audio = audio.flatten()
        return np.ascontiguousarray(audio)

    def iter_segments(self, audio: np.ndarray) -> Iterator[Segment]:
        audio = self._prepare(audio)
        assert self._model is not None
        dur = audio.size / SAMPLE_RATE
        if dur <= LONG_FORM_S or self._vad is None:
            with self._lock:
                text = self._model.recognize(audio, sample_rate=SAMPLE_RATE)
            text = (text or "").strip() if isinstance(text, str) else str(text).strip()
            if text:
                yield Segment(start=0.0, end=dur, text=text)
            return
        with self._lock:
            results = list(self._model.with_vad(self._vad).recognize(audio, sample_rate=SAMPLE_RATE))
        for r in results:
            text = (getattr(r, "text", "") or "").strip()
            if text:
                yield Segment(start=float(getattr(r, "start", 0.0)), end=float(getattr(r, "end", 0.0)), text=text)

    def transcribe(
        self,
        audio: np.ndarray,
        on_segment: Optional[Callable[[Segment], None]] = None,
    ) -> str:
        t0 = time.perf_counter()
        parts = []
        for seg in self.iter_segments(audio):
            parts.append(seg.text)
            if on_segment is not None:
                on_segment(seg)
        text = " ".join(parts).strip()
        log.info("transcribed %.2fs audio in %.2fs -> %d chars",
                 audio.size / SAMPLE_RATE, time.perf_counter() - t0, len(text))
        return text
