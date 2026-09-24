"""Always-open microphone with a pre-roll ring buffer.

The input stream is opened once and stays open while MURMUR is resident.
Audio flows into a short ring buffer at all times; `begin()` marks the
start of an utterance and keeps the last `preroll_ms` of the ring so the
first syllable is never lost to device-open latency or a late key press.
`end()` returns the utterance immediately: there is no in-flight read to
wait for.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque, List, Optional

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal

from murmur.config import BLOCK_SIZE, CHANNELS, SAMPLE_RATE

log = logging.getLogger(__name__)


class AudioCapture(QObject):
    """Long-lived capture. Signals are emitted from the audio callback
    thread; Qt queues them onto the GUI thread."""

    samples = Signal(np.ndarray)      # each block while recording (waveform feed)
    error = Signal(str)

    def __init__(self, device: Optional[int] = None, preroll_ms: int = 300, block: int = BLOCK_SIZE,
                 auto_open: bool = True):
        super().__init__()
        self.device = device
        self.preroll_ms = max(0, int(preroll_ms))
        self.block = block
        self.auto_open = auto_open      # False: audio is fed via _on_audio (tests, simulations)
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        self._ring: Deque[np.ndarray] = deque(maxlen=max(1, int(1.5 * SAMPLE_RATE / block)))  # 1.5 s
        self._chunks: List[np.ndarray] = []
        self._recording = False
        self._failed = False

    # --- stream lifecycle --------------------------------------------------

    def open(self) -> bool:
        if self._stream is not None:
            return True
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                device=self.device,
                blocksize=self.block,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()
            self._failed = False
            log.info("input stream open (device=%s, preroll=%dms)", self.device, self.preroll_ms)
            return True
        except Exception as e:
            log.exception("could not open input stream")
            self._stream = None
            self._failed = True
            self.error.emit(str(e))
            return False

    def close(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        with self._lock:
            self._recording = False
            self._chunks = []
            self._ring.clear()

    def set_device(self, device: Optional[int]) -> None:
        if device == self.device and self._stream is not None:
            return
        self.device = device
        self.close()
        self.open()

    def set_preroll(self, ms: int) -> None:
        self.preroll_ms = max(0, int(ms))

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    @property
    def recording(self) -> bool:
        return self._recording

    # --- utterance -----------------------------------------------------------

    def begin(self) -> bool:
        """Start an utterance. Keeps the last preroll_ms from the ring."""
        if self._stream is None and self.auto_open and not self.open():
            return False
        with self._lock:
            n_pre = int(self.preroll_ms * SAMPLE_RATE / 1000)
            pre: List[np.ndarray] = []
            if n_pre > 0 and self._ring:
                have = 0
                for blk in reversed(self._ring):
                    pre.insert(0, blk)
                    have += blk.size
                    if have >= n_pre:
                        break
                if have > n_pre and pre:
                    pre[0] = pre[0][-(n_pre - (have - pre[0].size)):] if have - pre[0].size < n_pre else pre[0][:0]
            self._chunks = pre
            self._recording = True
        return True

    def end(self) -> np.ndarray:
        """Stop the utterance and return it as float32 mono 16 kHz."""
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def peek(self) -> np.ndarray:
        """Everything recorded so far (for streaming partials)."""
        with self._lock:
            chunks = list(self._chunks)
        return np.concatenate(chunks).astype(np.float32, copy=False) if chunks else np.zeros(0, dtype=np.float32)

    # --- audio thread --------------------------------------------------------

    def _on_audio(self, indata, frames, _time, status) -> None:
        if status and status.input_overflow:
            log.debug("input overflow")
        chunk = np.array(indata[:, 0] if indata.ndim > 1 else indata, dtype=np.float32, copy=True)
        with self._lock:
            self._ring.append(chunk)
            rec = self._recording
            if rec:
                self._chunks.append(chunk)
        if rec:
            self.samples.emit(chunk)
